"""Setup for Vivit Energy Portal (Unofficial)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    LOGGER,
    UPDATE_INTERVAL,
    LOGIN_DATA,
    LOGIN_HEADERS,
    CONTRACTS_HEADERS,
    COOKIES_CONST,
    LOGIN_URL,
    CONTRACTS_URL,
    HOUSES_URL,
    INVOICES_URL,
    COSTS_URL,
    NEXT_INVOICE_URL,
    VIRTUAL_BATTERY_HISTORY_URL,
)

PLATFORMS: list[str] = ["sensor"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    session = async_get_clientsession(hass)

    selected_contract_id: Optional[str] = entry.data.get("contract_id")

    client = RepsolAPI(
        session=session,
        username=entry.data["username"],
        password=entry.data["password"],
    )

    # 1) Login una vez
    await client.async_login()

    # 2) Obtener TODOS los contratos para calcular índice y tipo
    contracts_overview = await client.async_get_contracts()
    if not contracts_overview:
        raise UpdateFailed("No se pudieron obtener contratos para calcular el índice.")

    all_contracts: List[Dict[str, Any]] = contracts_overview.get("information", [])
    # Orden estable por contract_id para asignar índices deterministas
    ordered_ids = sorted([c["contract_id"] for c in all_contracts])

    if selected_contract_id is None:
        # Si la entrada no restringe, coger el primero por compatibilidad
        selected_contract_id = ordered_ids[0] if ordered_ids else None

    if not selected_contract_id or selected_contract_id not in ordered_ids:
        raise UpdateFailed("El contract_id seleccionado no existe en la cuenta.")

    contract_index = ordered_ids.index(selected_contract_id) + 1
    selected_contract_info = next(c for c in all_contracts if c["contract_id"] == selected_contract_id)
    contract_type = (selected_contract_info.get("contractType") or "ELECTRICITY").upper()

    # Guardamos etiquetas/nombres para usar en sensor.py (device_info)
    contract_label = f"Contrato {contract_index}"
    device_name = f"{contract_label} ({'Electricidad' if contract_type == 'ELECTRICITY' else 'Gas'})"

    # 3) La actualización del coordinator solo trae el contrato seleccionado
    async def _async_update_data():
        try:
            return await client.fetch_all_data(selected_only_id=selected_contract_id)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error actualizando datos: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        name=f"{DOMAIN}-coordinator",
        update_method=_async_update_data,
        update_interval=UPDATE_INTERVAL,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": client,
        "coordinator": coordinator,
        "contract_id": selected_contract_id,
        "contract_type": contract_type,
        "contract_label": contract_label,  # "Contrato N"
        "device_name": device_name,        # "Contrato N (Electricidad)"
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


# -------------------- Cliente API -------------------- #
class RepsolAPI:
    def __init__(self, session: aiohttp.ClientSession, username: str, password: str):
        self.session = session
        self.username = username
        self.password = password
        self.uid: Optional[str] = None
        self.signature: Optional[str] = None
        self.timestamp: Optional[str] = None
        self.cookies = COOKIES_CONST.copy()

    async def async_login(self):
        data = LOGIN_DATA.copy()
        data.update({"loginID": self.username, "password": self.password})
        headers = LOGIN_HEADERS.copy()

        async with self.session.post(
            LOGIN_URL, headers=headers, cookies=self.cookies, data=data
        ) as resp:
            body = await resp.text()
            if resp.status != 200:
                LOGGER.error("Login failed. HTTP %s Body=%s", resp.status, body[:500])
                raise RuntimeError("login_failed_http")

            payload = await resp.json(content_type=None)
            ui = payload.get("userInfo") or {}
            self.uid = ui.get("UID")
            self.signature = ui.get("UIDSignature")
            self.timestamp = ui.get("signatureTimestamp")
            if not (self.uid and self.signature and self.timestamp):
                LOGGER.error("Login tokens missing. userInfo=%s", ui)
                raise RuntimeError("login_failed_tokens")

    def _auth_headers(self) -> Dict[str, str]:
        h = CONTRACTS_HEADERS.copy()
        h.update(
            {"UID": self.uid or "", "signature": self.signature or "", "signatureTimestamp": self.timestamp or ""}
        )
        return h

    async def async_get_contracts(self) -> Optional[Dict[str, Any]]:
        url = CONTRACTS_URL
        headers = self._auth_headers()
        async with self.session.get(url, headers=headers, cookies=self.cookies) as resp:
            if resp.status != 200:
                LOGGER.error("Contracts fetch failed. HTTP %s", resp.status)
                return None
            raw = await resp.json(content_type=None)

        contracts: Dict[str, Any] = {"information": []}
        for house in raw or []:
            hid = house.get("code")
            for c in (house.get("contracts") or []):
                contracts["information"].append(
                    {
                        "contract_id": c.get("code"),
                        "contractType": c.get("contractType"),
                        "cups": c.get("cups"),
                        "active": c.get("status") == "ACTIVE",
                        "house_id": hid,
                    }
                )
        return contracts

    async def async_get_house(self, house_id: str) -> Optional[Dict[str, Any]]:
        url = HOUSES_URL.format(house_id)
        async with self.session.get(url, headers=self._auth_headers(), cookies=self.cookies) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)

    async def async_get_invoices(self, house_id: str, contract_id: str) -> Optional[Any]:
        url = INVOICES_URL.format(house_id, contract_id)
        async with self.session.get(url, headers=self._auth_headers(), cookies=self.cookies) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)

    async def async_get_costs(self, house_id: str, contract_id: str) -> Dict[str, Any]:
        url = COSTS_URL.format(house_id, contract_id)
        base = {
            "totalDays": 0,
            "consumption": 0,
            "amount": 0,
            "amountVariable": 0,
            "amountFixed": 0,
            "averageAmount": 0,
        }
        async with self.session.get(url, headers=self._auth_headers(), cookies=self.cookies) as resp:
            if resp.status != 200:
                return base
            data = await resp.json(content_type=None)
        for k in base:
            base[k] = data.get(k, 0)
        return base

    async def async_get_next_invoice(self, house_id: str, contract_id: str) -> Dict[str, Any]:
        url = NEXT_INVOICE_URL.format(house_id, contract_id)
        base = {"amount": 0, "amountVariable": 0, "amountFixed": 0}
        async with self.session.get(url, headers=self._auth_headers(), cookies=self.cookies) as resp:
            if resp.status != 200:
                return base
            data = await resp.json(content_type=None)
        for k in base:
            base[k] = data.get(k, 0)
        return base

    async def async_get_vb_history(self, house_id: str, contract_id: str) -> Optional[Dict[str, Any]]:
        url = VIRTUAL_BATTERY_HISTORY_URL.format(house_id, contract_id)
        async with self.session.get(url, headers=self._auth_headers(), cookies=self.cookies) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)

    async def fetch_all_data(self, selected_only_id: Optional[str] = None) -> Dict[str, Any]:
        """Devuelve datos de 1 contrato (si selected_only_id) o todos."""
        overview = await self.async_get_contracts()
        if not overview:
            raise RuntimeError("No contracts found")
        contracts = overview.get("information", [])

        if selected_only_id:
            contracts = [c for c in contracts if c["contract_id"] == selected_only_id]
            if not contracts:
                raise RuntimeError("Selected contract not found")

        result: Dict[str, Any] = {}

        for c in contracts:
            house_id = c["house_id"]
            contract_id = c["contract_id"]

            house = await self.async_get_house(house_id)
            invoices = await self.async_get_invoices(house_id, contract_id)
            costs = await self.async_get_costs(house_id, contract_id)
            next_inv = await self.async_get_next_invoice(house_id, contract_id)
            vb = None
            if (c.get("contractType") or "").upper() == "ELECTRICITY":
                vb = await self.async_get_vb_history(house_id, contract_id)

            result[contract_id] = {
                "contracts": c,
                "house_data": house,
                "invoices": invoices,
                "costs": costs,
                "nextInvoice": next_inv,
                "virtual_battery_history": vb,
            }

        return result
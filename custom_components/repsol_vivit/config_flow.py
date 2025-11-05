"""Config flow for Vivit Energy (unofficial)."""
from __future__ import annotations

from typing import Any
import asyncio
import voluptuous as vol
from aiohttp.client_exceptions import ClientConnectorError, ClientResponseError

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    LOGGER,
    LOGIN_URL,
    CONTRACTS_URL,
    LOGIN_HEADERS,
    CONTRACTS_HEADERS,
    COOKIES_CONST,
    LOGIN_DATA,
)

REQ_TIMEOUT = 15


class RepsolConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._creds: dict[str, Any] | None = None
        self._contracts: list[dict[str, Any]] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            cookies = COOKIES_CONST.copy()

            # ---- LOGIN ----
            data = LOGIN_DATA.copy()
            data.update({"loginID": user_input["username"], "password": user_input["password"]})
            headers = LOGIN_HEADERS.copy()

            try:
                async with asyncio.timeout(REQ_TIMEOUT):
                    async with session.post(
                        LOGIN_URL, headers=headers, cookies=cookies, data=data
                    ) as resp:
                        if resp.status != 200:
                            body = (await resp.text())[:500]
                            LOGGER.error("Login failed in flow. HTTP %s Body=%s", resp.status, body)
                            errors["base"] = "invalid_auth" if resp.status in (401, 403) else "cannot_connect"
                        else:
                            payload = await resp.json(content_type=None)
                            ui = payload.get("userInfo") or {}
                            uid = ui.get("UID")
                            sig = ui.get("UIDSignature")
                            ts = ui.get("signatureTimestamp")
                            if not (uid and sig and ts):
                                LOGGER.error("Login tokens missing in flow. userInfo=%s", ui)
                                errors["base"] = "invalid_auth"
                            else:
                                # ---- CONTRACTS ----
                                headers2 = CONTRACTS_HEADERS.copy()
                                headers2.update(
                                    {"UID": uid, "signature": sig, "signatureTimestamp": ts}
                                )
                                async with asyncio.timeout(REQ_TIMEOUT):
                                    async with session.get(
                                        CONTRACTS_URL, headers=headers2, cookies=cookies
                                    ) as r2:
                                        body2 = (await r2.text()) if r2.status != 200 else None
                                        if r2.status != 200:
                                            LOGGER.error(
                                                "Contracts fetch failed in flow. HTTP %s Body=%s",
                                                r2.status, (body2 or "")[:500]
                                            )
                                            errors["base"] = "cannot_connect"
                                        else:
                                            data2 = await r2.json(content_type=None)
                                            contracts: list[dict[str, Any]] = []
                                            for house in data2 or []:
                                                hid = (house or {}).get("code")
                                                for c in (house or {}).get("contracts", []):
                                                    contracts.append(
                                                        {
                                                            "code": c.get("code"),
                                                            "cups": c.get("cups"),
                                                            "type": c.get("contractType"),
                                                            "house_id": hid,
                                                        }
                                                    )

                                            if not contracts:
                                                errors["base"] = "no_contracts"
                                            else:
                                                # guardamos orden tal cual llega para index estable
                                                self._creds = {
                                                    "username": user_input["username"],
                                                    "password": user_input["password"],
                                                }
                                                self._contracts = contracts
                                                return await self.async_step_contract()

            except (ClientConnectorError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            except ClientResponseError as e:
                errors["base"] = "invalid_auth" if e.status in (401, 403) else "cannot_connect"
            except Exception as e:  # noqa: BLE001
                LOGGER.exception("Unexpected flow error: %s", e)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("username", default=(user_input or {}).get("username", "")): str,
                    vol.Required("password"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_contract(self, user_input: dict[str, Any] | None = None):
        assert self._contracts is not None

        # Mapa para el selector: code -> "TYPE - CUPS"
        opts = {
            c["code"]: f'{(c.get("type") or "").upper()} - {c.get("cups") or ""}'
            for c in self._contracts
        }

        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["contract_code"]
            selected = next(c for c in self._contracts if c["code"] == code)

            # Índice 1-basado, estable según orden devuelto
            idx = next(i for i, c in enumerate(self._contracts, start=1) if c["code"] == code)

            title = f'{(selected.get("type") or "ELECTRICITY").upper()} - {selected.get("cups") or code}'

            data = {
                **(self._creds or {}),
                "contract_id": selected["code"],
                "contract_index": idx,            # <- guardamos el número de contrato
                "contract_type": selected.get("type") or "ELECTRICITY",
                "house_id": selected.get("house_id"),
            }

            await self.async_set_unique_id(f"{DOMAIN}_{selected['code']}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="contract",
            data_schema=vol.Schema({vol.Required("contract_code"): vol.In(opts)}),
            errors=errors,
        )
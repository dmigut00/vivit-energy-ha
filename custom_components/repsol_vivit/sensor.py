"""Sensors for Vivit Energy Portal (Unofficial)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER

# Nombres LIMPIOS para sensores (sin “Contrato N”)
SENSOR_DEFS = [
    {"name": "Consumo", "var": "consumption", "class": SensorDeviceClass.ENERGY},
    {"name": "Días totales", "var": "totalDays", "class": None},
    {"name": "Estado contrato", "var": "status", "class": None},
    {"name": "Importe", "var": "amount", "class": SensorDeviceClass.MONETARY},
    {"name": "Importe fijo", "var": "amountFixed", "class": SensorDeviceClass.MONETARY},
    {"name": "Importe variable", "var": "amountVariable", "class": SensorDeviceClass.MONETARY},
    {"name": "Promedio diario", "var": "averageAmount", "class": SensorDeviceClass.MONETARY},
    {"name": "Última factura", "var": "lastInvoiceAmount", "class": SensorDeviceClass.MONETARY},
    {"name": "Última factura pagada", "var": "lastInvoicePaid", "class": None},
    {"name": "Próxima factura", "var": "nextInvoiceAmount", "class": SensorDeviceClass.MONETARY},
    {"name": "Variable próxima factura", "var": "nextInvoiceVariableAmount", "class": SensorDeviceClass.MONETARY},
    {"name": "Fijo próxima factura", "var": "nextInvoiceFixedAmount", "class": SensorDeviceClass.MONETARY},
    {"name": "Potencia contratada", "var": "power", "class": SensorDeviceClass.POWER},
    {"name": "Tarifa", "var": "fee", "class": None},
    {"name": "Precio potencia punta", "var": "pricesPowerPunta", "class": SensorDeviceClass.MONETARY},
    {"name": "Precio potencia valle", "var": "pricesPowerValle", "class": SensorDeviceClass.MONETARY},
    {"name": "Precio energía", "var": "pricesEnergyAmount", "class": SensorDeviceClass.MONETARY},
]

VB_DEFS = [
    {"name": "Batería virtual — € pendientes", "var": "pendingAmount", "class": SensorDeviceClass.MONETARY},
    {"name": "Batería virtual — kWh disponibles", "var": "kwhAvailable", "class": SensorDeviceClass.ENERGY},
    {"name": "Batería virtual — € canjeados", "var": "appliedAmount", "class": SensorDeviceClass.MONETARY},
    {"name": "Batería virtual — kWh canjeados", "var": "kwhRedeemed", "class": SensorDeviceClass.ENERGY},
    {"name": "Batería virtual — kWh totales", "var": "totalKWh", "class": SensorDeviceClass.ENERGY},
    {"name": "Batería virtual — precio excedentes", "var": "excedentsPrice", "class": SensorDeviceClass.MONETARY},
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    stored = hass.data[DOMAIN][entry.entry_id]
    coordinator = stored["coordinator"]
    contract_id = stored["contract_id"]
    contract_type = stored["contract_type"]
    device_name = stored["device_name"]  # “Contrato N (Electricidad)”

    data: Dict[str, Dict[str, Any]] = coordinator.data or {}
    payload = data.get(contract_id)
    if not payload:
        LOGGER.error("No payload for contract_id %s", contract_id)
        return

    cinfo = payload.get("contracts") or {}
    cups = cinfo.get("cups") or contract_id
    house_id = cinfo.get("house_id")
    house_data = payload.get("house_data") or {}
    house_contracts = house_data.get("contracts") or []
    house_contract = next((c for c in house_contracts if c.get("code") == contract_id), {})

    entities: List[SensorEntity] = []

    # Sensores “core”
    for sd in SENSOR_DEFS:
        # Para GAS filtramos los que aplican (reutilizamos tu criterio: potencia/tarifa/precios solo electricidad)
        if contract_type == "GAS" and sd["var"] in {
            "power", "fee", "pricesPowerPunta", "pricesPowerValle", "pricesEnergyAmount"
        }:
            continue

        entities.append(
            VivitSensor(
                coordinator=coordinator,
                name=sd["name"],            # nombre limpio
                variable=sd["var"],
                device_class=sd["class"],
                device_name=device_name,    # nombre del dispositivo
                house_id=house_id,
                contract_id=contract_id,
                contract_type=contract_type,
                cups=cups,
                contract_info=cinfo,
                house_contract=house_contract,
            )
        )

    # Batería virtual (solo electricidad)
    if contract_type == "ELECTRICITY":
        vb = payload.get("virtual_battery_history")
        if vb:
            for sd in VB_DEFS:
                entities.append(
                    VivitVBSensor(
                        coordinator=coordinator,
                        name=sd["name"],
                        variable=sd["var"],
                        device_class=sd["class"],
                        device_name=device_name,
                        house_id=house_id,
                        contract_id=contract_id,
                        vb_data=vb,
                    )
                )

            # Último canje (si existe)
            last_red = max((vb.get("discounts", {}) or {}).get("data", []), key=lambda x: x.get("billingDate", ""), default=None)
            if last_red:
                entities.append(
                    VivitVBSensor(
                        coordinator=coordinator,
                        name="Batería virtual — último importe canjeado",
                        variable="amount",
                        device_class=SensorDeviceClass.MONETARY,
                        device_name=device_name,
                        house_id=house_id,
                        contract_id=contract_id,
                        coupon_data=last_red,
                    )
                )
                entities.append(
                    VivitVBSensor(
                        coordinator=coordinator,
                        name="Batería virtual — últimos kWh canjeados",
                        variable="kWh",
                        device_class=SensorDeviceClass.ENERGY,
                        device_name=device_name,
                        house_id=house_id,
                        contract_id=contract_id,
                        coupon_data=last_red,
                    )
                )

    async_add_entities(entities, True)


class VivitBase(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator,
        name: str,
        variable: str,
        device_class: Optional[SensorDeviceClass],
        device_name: str,
        house_id: str,
        contract_id: str,
    ):
        super().__init__(coordinator)
        self._attr_name = name                  # nombre visible del sensor (limpio)
        self.variable = variable
        self._attr_device_class = device_class
        self.device_name = device_name          # nombre del dispositivo
        self.house_id = house_id
        self.contract_id = contract_id

    @property
    def unique_id(self) -> str:
        return f"{self.house_id}_{self.contract_id}_{self.variable}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.house_id}_{self.contract_id}")},
            name=self.device_name,  # p.ej. "Contrato 2 (Electricidad)"
            manufacturer="Vivit Energy (unofficial)",
            model="Portal",
            serial_number=str(self.contract_id),
        )

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        user_currency = self.hass.config.currency
        if self._attr_device_class == SensorDeviceClass.ENERGY:
            return "kWh"
        if self._attr_device_class == SensorDeviceClass.MONETARY:
            # variables de precio por kWh
            if self.variable in ("pricesEnergyAmount", "excedentsPrice"):
                return f"{user_currency}/kWh"
            return user_currency
        if self._attr_device_class == SensorDeviceClass.POWER:
            return "kW"
        return None


class VivitSensor(VivitBase):
    def __init__(
        self,
        coordinator,
        name: str,
        variable: str,
        device_class: Optional[SensorDeviceClass],
        device_name: str,
        house_id: str,
        contract_id: str,
        contract_type: str,
        cups: str,
        contract_info: Dict[str, Any],
        house_contract: Dict[str, Any],
    ):
        super().__init__(coordinator, name, variable, device_class, device_name, house_id, contract_id)
        self.contract_type = contract_type
        self.cups = cups
        self.contract_info = contract_info or {}
        self.house_contract = house_contract or {}

    @property
    def native_value(self) -> Any:
        data = (self.coordinator.data or {}).get(self.contract_id) or {}

        # Costes
        if self.variable in {"amount", "consumption", "totalDays", "amountVariable", "amountFixed", "averageAmount"}:
            return (data.get("costs") or {}).get(self.variable)

        # Última factura
        if self.variable in {"lastInvoiceAmount", "lastInvoicePaid"}:
            inv = data.get("invoices")
            obj = None
            if isinstance(inv, list) and inv:
                obj = inv[0]
            elif isinstance(inv, dict):
                obj = inv
            if not obj:
                return None
            if self.variable == "lastInvoiceAmount":
                return obj.get("amount") or obj.get("totalAmount")
            return "Yes" if (obj.get("status") == "PAID") else "No"

        # Próxima factura
        if self.variable in {"nextInvoiceAmount", "nextInvoiceVariableAmount", "nextInvoiceFixedAmount"}:
            nxt = data.get("nextInvoice") or {}
            return {
                "nextInvoiceAmount": nxt.get("amount"),
                "nextInvoiceVariableAmount": nxt.get("amountVariable"),
                "nextInvoiceFixedAmount": nxt.get("amountFixed"),
            }.get(self.variable)

        # Datos de contrato/tarifa
        if self.contract_type == "ELECTRICITY":
            if self.variable in {"status", "power", "fee"}:
                return self.house_contract.get(self.variable) or self.contract_info.get(self.variable)

            prices = (self.house_contract.get("prices") or self.contract_info.get("prices") or {})
            if self.variable == "pricesPowerPunta":
                return _parse_price_list((prices.get("power") or []), 0)
            if self.variable == "pricesPowerValle":
                return _parse_price_list((prices.get("power") or []), 1)
            if self.variable == "pricesEnergyAmount":
                return _parse_price_list((prices.get("energy") or []), 0)

        return None


class VivitVBSensor(VivitBase):
    def __init__(
        self,
        coordinator,
        name: str,
        variable: str,
        device_class: Optional[SensorDeviceClass],
        device_name: str,
        house_id: str,
        contract_id: str,
        vb_data: Optional[Dict[str, Any]] = None,
        coupon_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(coordinator, name, variable, device_class, device_name, house_id, contract_id)
        self.vb_data = vb_data or {}
        self.coupon_data = coupon_data

    @property
    def native_value(self) -> Any:
        if self.coupon_data:
            return self.coupon_data.get(self.variable)

        vb = self.vb_data
        discounts = vb.get("discounts") or {}
        excedents = vb.get("excedents") or {}

        if self.variable == "pendingAmount":
            c = next((c for c in (discounts.get("contracts") or []) if c.get("productCode") == self.contract_id), None)
            return c.get("pendingAmount") if c else None

        if self.variable == "kwhAvailable":
            c = next((c for c in (discounts.get("contracts") or []) if c.get("productCode") == self.contract_id), None)
            pending = c.get("pendingAmount") if c else 0
            conv = next((d.get("conversionPrice") for d in (excedents.get("data") or [])), 0)
            try:
                return round(float(pending) / float(conv), 2) if conv else None
            except Exception:
                return None

        if self.variable == "appliedAmount":
            return excedents.get("appliedAmount")

        if self.variable == "kwhRedeemed":
            conv = next((d.get("conversionPrice") for d in (excedents.get("data") or [])), 0)
            try:
                return round(float(excedents.get("appliedAmount", 0)) / float(conv), 2) if conv else None
            except Exception:
                return None

        if self.variable == "totalKWh":
            try:
                return round(float(excedents.get("totalkWh", 0)), 2)
            except Exception:
                return None

        if self.variable == "excedentsPrice":
            return next((d.get("conversionPrice") for d in (excedents.get("data") or [])), None)

        return None


def _parse_price_list(prices: List[str], index: int) -> Any:
    """Extrae 0,1234 de strings y retorna como '0.1234'."""
    parsed: List[str] = []
    for p in prices:
        m = re.search(r"(\d+,\d+)", str(p))
        if m:
            parsed.append(m.group(1).replace(",", "."))
    return parsed[index] if index < len(parsed) else None
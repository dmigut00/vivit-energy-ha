# 📘 Changelog — Vivit Energy Portal (No oficial)

## 🆕 v1.1.0 — 4 de noviembre de 2025

### 🚀 Mejoras principales
- Los contratos ahora se renombran automáticamente como **“Contrato 1”, “Contrato 2”, etc.**
- Los **nombres de sensores son más limpios y legibles**, sin el prefijo “Contrato N”.
- Los **dispositivos** muestran claramente su tipo:  
  `Contrato 1 (Electricidad)` o `Contrato 2 (Gas)`.
- Compatibilidad completa con **múltiples contratos** (eléctricos y de gas).
- Mejora general de la estabilidad y fiabilidad en la actualización de datos.

### 🔧 Cambios técnicos
- Refactorización total de `__init__.py` y `sensor.py`.
- Nuevo sistema de identificación (`unique_id`) que evita conflictos entre contratos.
- Corrección de errores en la obtención de datos de facturas y batería virtual.
- Manejo de errores más detallado y robusto ante respuestas de la API.
- Traducciones actualizadas en **español, inglés y portugués**.
- Preparación de base para futuras funciones (nuevos sensores, batería virtual extendida, histórico diario, etc.).

---

## 🏁 v1.0.0 — 4 de noviembre de 2025

### ✨ Primera versión estable
- Autenticación con las credenciales del **portal Vivit Energy (Repsol Luz y Gas)**.
- Descarga de información de **costes, consumos y facturas**.
- Soporte inicial para la **batería virtual**.
- Actualización automática de datos cada 2 horas.
- Compatibilidad con múltiples contratos y tipos (Electricidad / Gas).
- Integración totalmente funcional con interfaz de configuración (`config_flow`).

---

📦 **Autor:** [@s3rp1](https://codeberg.org/s3rp102)  
📅 **Última actualización:** 4 de noviembre de 2025  
🔖 **Versión actual:** v1.1.0  
💡 **Tipo de integración:** `cloud_polling`
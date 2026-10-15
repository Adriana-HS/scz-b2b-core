## Qué cambia y por qué

<!-- Enlaza el issue, la sección de SYSTEM_DESIGN.md o el ADR -->

## Tipo
- [ ] feat  - [ ] fix  - [ ] docs  - [ ] refactor  - [ ] chore  - [ ] migración de BD

## Checklist
- [ ] Pruebas agregadas o actualizadas (pgTAP si cambia el esquema)
- [ ] Toda tabla nueva tiene `organization_id`, RLS, políticas y prueba de aislamiento
- [ ] Sin secretos ni datos reales de clientes
- [ ] Si toca dinero, autenticación o datos personales: revisión de seguridad solicitada
- [ ] ADR nuevo o actualizado si cambia una decisión de arquitectura
- [ ] Documentación y CHANGELOG actualizados

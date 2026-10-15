# Modelos UML

Diagramas UML 2.5.1 expresados en Mermaid (GitHub los renderiza). Las vistas de contexto y contenedores (C4), la secuencia de mensajes y pagos, la FSM de órdenes y el modelo entidad-relación están en `SYSTEM_DESIGN.md` §4–6. Aquí van las vistas que faltaban: casos de uso, clases del dominio, despliegue, actividad y estados auxiliares.

> Convención: los nombres en inglés corresponden al código; las descripciones están en español. Cada diagrama cita los requisitos (`RF-xx`) del `docs/requirements/SRS.md` que modela.

## 1. Diagrama de casos de uso

```mermaid
flowchart LR
    C(["👤 Cliente / Paciente"])
    D(["👤 Dueño / Admin"])
    S(["👤 Personal (STAFF)"])
    A(["👤 Auditor"])
    B(["⚙️ Banco / OpenBCB"])
    M(["⚙️ WhatsApp (Meta)"])
    SI(["⚙️ Proveedor SIAT"])

    subgraph SCZ["SCZ-B2B-Core"]
        UC1(("UC-01 Consultar catálogo<br/>y precios"))
        UC2(("UC-02 Solicitar cotización"))
        UC3(("UC-03 Agendar cita"))
        UC4(("UC-04 Pagar con QR"))
        UC5(("UC-05 Enviar comprobante"))
        UC6(("UC-06 Hablar con una persona"))
        UC7(("UC-07 Gestionar catálogo"))
        UC8(("UC-08 Registrar tipo de cambio"))
        UC9(("UC-09 Revisar evidencias de pago"))
        UC10(("UC-10 Atender conversación<br/>(modo humano)"))
        UC11(("UC-11 Gestionar agenda<br/>y órdenes"))
        UC12(("UC-12 Consultar auditoría<br/>y reportes"))
        UC13(("UC-13 Emitir factura"))
    end

    C --- UC1 & UC2 & UC3 & UC4 & UC5 & UC6
    M --- UC1
    S --- UC7 & UC10 & UC11
    D --- UC7 & UC8 & UC9 & UC10 & UC11 & UC12
    A --- UC12
    UC4 --- B
    UC13 --- SI
    UC2 -.->|"«include»"| UC1
    UC5 -.->|"«extend»"| UC4
    UC13 -.->|"«extend»"| UC9
```

## 2. Diagrama de clases del dominio

Modela los agregados, *value objects*, políticas y puertos (RF-02…RF-12).

```mermaid
classDiagram
    direction LR

    class Organization {
        +UUID id
        +BusinessVertical vertical
        +str timezone
        +Percent depositPercent
        +int quoteValidityHours
        +TenantConfig config
    }
    class CatalogItem {
        +UUID id
        +ItemType type
        +Sku sku
        +str name
        +Money listPrice
        +int? stockOnHand
        +int? durationMinutes
    }
    class Order {
        +UUID id
        +OrderKind kind
        +ReferenceCode reference
        +OrderStatus status
        +FxRate? appliedFx
        +TaxBreakdown taxes
        +Money depositRequired
        +Money amountPaid
        +Slot? slot
        +transitionTo(status, actor) void
        +isCovered() bool
    }
    class OrderLine {
        +UUID catalogItemId
        +str skuSnapshot
        +int quantity
        +Money unitPrice
        +lineTotal() Money
    }
    class PaymentEvidence {
        +UUID id
        +Sha256 hash
        +PHash phash
        +ReceiptExtraction extracted
        +EvidenceStatus status
        +RuleResult[] checks
    }
    class Payment {
        +UUID id
        +Money amount
        +Confirmation via
    }
    class Conversation {
        +UUID id
        +ConversationMode mode
        +datetime lastInboundAt
        +isWindowOpen(now) bool
    }

    class Money {
        <<ValueObject>>
        +Decimal amount
        +Currency currency
        +add(Money) Money
        +round2() Money
    }
    class FxRate {
        <<ValueObject>>
        +Decimal bsPerUsd
        +FxSource source
        +datetime effectiveAt
        +isStale(maxAge) bool
    }
    class TaxBreakdown {
        <<ValueObject>>
        +TaxMode mode
        +Money subtotal
        +Money iva
        +Money total
    }
    class Slot {
        <<ValueObject>>
        +datetime start
        +datetime end
        +overlaps(Slot) bool
    }

    class VerticalPolicy {
        <<interface>>
        +systemPrompt(cfg) str
        +enabledTools() ToolSpec[]
        +configSchema() type
    }
    class ImportadoraPolicy
    class ClinicaPolicy
    VerticalPolicy <|.. ImportadoraPolicy
    VerticalPolicy <|.. ClinicaPolicy

    class FraudRule {
        <<interface>>
        +evaluate(evidence, order, ctx) RuleResult
    }
    class AmountRule
    class BeneficiaryRule
    class DateWindowRule
    class PerceptualHashRule
    FraudRule <|.. AmountRule
    FraudRule <|.. BeneficiaryRule
    FraudRule <|.. DateWindowRule
    FraudRule <|.. PerceptualHashRule

    class PaymentProvider {
        <<interface>>
        +createCharge(ref, amount, ttl) ChargeQR
        +verify(notification) PaymentConfirmed?
    }
    class BankApiProvider
    class OpenBcbProvider
    class StaticQrProvider
    PaymentProvider <|.. BankApiProvider
    PaymentProvider <|.. OpenBcbProvider
    PaymentProvider <|.. StaticQrProvider

    Organization "1" --> "*" CatalogItem
    Organization "1" --> "1" VerticalPolicy : usa
    Organization "1" --> "*" Order
    Order "1" *-- "1..*" OrderLine
    Order "1" --> "*" PaymentEvidence
    Order "1" --> "*" Payment
    Order --> TaxBreakdown
    Order --> FxRate
    Order --> Slot
    OrderLine --> Money
    PaymentEvidence --> FraudRule : evaluada por
    Conversation "1" --> "*" Order
```

## 3. Diagrama de componentes y paquetes

```mermaid
flowchart TB
    subgraph backend["«subsystem» backend/app"]
        direction TB
        api["«component» api.v1"]
        workers["«component» workers"]
        app["«component» application<br/>(use_cases, agent, guardrails)"]
        verticals["«component» verticals<br/>(importadora, clinica)"]
        domain["«component» domain<br/>(entities, value_objects, rules, ports)"]
        infra["«component» infrastructure<br/>(db, messaging, ai, payments, billing, storage, pdf)"]
    end
    frontend["«subsystem» frontend (Next.js)"]
    db[("«database» PostgreSQL + pgvector")]

    api --> app
    workers --> app
    app --> domain
    verticals --> domain
    app --> verticals
    infra -.->|"«realize» puertos"| domain
    infra --> db
    frontend --> db
    frontend --> api
```

## 4. Diagrama de despliegue

```mermaid
flowchart TB
    subgraph user["Dispositivos"]
        phone["«device» Celular del cliente<br/>WhatsApp"]
        pc["«device» PC / celular del negocio<br/>navegador"]
    end
    subgraph meta["«execution environment» Meta Cloud"]
        wa["WhatsApp Cloud API"]
    end
    subgraph region["«region» São Paulo"]
        subgraph compute["«execution environment» Contenedores (min. 1 instancia c/u)"]
            apiN["«artifact» scz-backend:tag<br/>proceso: api (uvicorn)"]
            wrkN["«artifact» scz-backend:tag<br/>proceso: worker"]
        end
        subgraph supa["«execution environment» Supabase Pro"]
            pg[("PostgreSQL + pgvector<br/>Supavisor :6543")]
            auth["Auth (JWT, MFA)"]
            st["Storage (buckets privados)"]
        end
    end
    subgraph vercel["«execution environment» Vercel"]
        web["«artifact» frontend (Next.js)"]
    end
    subgraph ext["Proveedores"]
        llm["LLM / visión / embeddings / STT"]
        bank["API QR del banco / OpenBCB"]
        siat["Proveedor SIAT"]
        obs["Observabilidad (OTel, Sentry)"]
    end

    phone <-->|HTTPS| wa
    wa -->|"webhook HTTPS + HMAC"| apiN
    wrkN -->|"Graph API HTTPS"| wa
    apiN & wrkN -->|"TLS, rol app_backend"| pg
    wrkN --> st
    wrkN --> llm
    wrkN <--> bank
    bank -->|"callback"| apiN
    wrkN -.-> siat
    pc -->|HTTPS| web
    web --> auth
    web -->|"supabase-js (JWT, RLS)"| pg
    apiN & wrkN -.-> obs
```

## 5. Diagrama de actividad — verificación de un comprobante (camino B)

Modela RF-08 y ADR-0008. Cada regla es un eslabón de la *Chain of Responsibility*.

```mermaid
flowchart TD
    start((●)) --> recv["Recibir imagen (media_id)"]
    recv --> dl["Descargar de Meta<br/>validar tamaño y magic bytes"]
    dl -->|inválida| rej1["Responder: formato no válido"] --> fin((◉))
    dl --> hash["SHA-256 de bytes originales<br/>+ pHash"]
    hash --> ins{"INSERT evidencia<br/>(UNIQUE por sha256)"}
    ins -->|duplicada| dup["Registrar REJECTED_DUPLICATE<br/>Avisar al cliente"] --> fin
    ins -->|nueva| ocr["Extracción estructurada (LLM visión, sin tools)"]
    ocr --> rules["Evaluar reglas en orden"]
    rules --> r1{"¿Monto = esperado?"}
    r1 -->|no| flag["Marcar regla fallida"]
    r1 -->|sí| r2{"¿Beneficiario / cuenta<br/>= cuenta del negocio?"}
    r2 -->|no| flag
    r2 -->|sí| r3{"¿Fecha dentro de la ventana<br/>(≥ creación de la orden, ≤ ahora)?"}
    r3 -->|no| flag
    r3 -->|sí| r4{"¿pHash lejos de evidencias previas?"}
    r4 -->|no| flag
    r4 -->|sí| r5{"¿Confianza OCR ≥ umbral?"}
    r5 -->|no| flag
    r5 -->|sí| pass["AUTO_CHECK_PASSED"]
    flag --> review["NEEDS_REVIEW (prioridad alta)"]
    pass --> txn{"UNIQUE global<br/>banco + nro. transacción"}
    review --> txn
    txn -->|ya usada| dup
    txn -->|ok| ord["Orden → PAYMENT_REVIEW"]
    ord --> ack["Cliente: 'Recibido, en verificación'"]
    ack --> human{{"Dueño con MFA decide"}}
    human -->|aprueba| pay["INSERT payments (HUMAN)<br/>trigger → CONFIRMED"]
    human -->|rechaza| back["Evidencia REJECTED_*<br/>Orden → AWAITING_PAYMENT"]
    pay --> fin
    back --> fin
```

## 6. Diagrama de actividad — turno del agente

Modela RF-01…RF-04 y §7 del diseño (*Template Method*).

```mermaid
flowchart TD
    s((●)) --> claim["Worker reclama PROCESS_CONVERSATION"]
    claim --> mode{"¿Conversación en modo HUMAN<br/>o bot pausado?"}
    mode -->|sí| skip["Solo registrar y notificar al equipo"] --> e((◉))
    mode -->|no| quota{"¿Dentro de las cuotas<br/>de cliente y tenant?"}
    quota -->|no| tmpl["Respuesta plantilla + handoff"] --> send
    quota -->|sí| ctx["Cargar contexto: política del rubro,<br/>resumen, últimos N mensajes"]
    ctx --> llm1["LLM: decidir (≤ 2 rondas, ≤ 4 tools)"]
    llm1 --> tool{"¿tool_use?"}
    tool -->|sí| val["Validar comando (Pydantic, extra=forbid)<br/>inyectar tenant/cliente"]
    val --> exec["Ejecutar RPC / caso de uso"]
    exec --> llm1
    tool -->|no| guard{"Guardrails: grounding numérico,<br/>alcance del negocio"}
    guard -->|falla| safe["Reemplazar por plantilla segura"] --> send
    guard -->|ok| send["Outbox (idempotency_key)"]
    send --> wa["Enviar a Meta (reintentos)"]
    wa --> done["finish_job · métricas · costo"] --> e
```

## 7. Diagramas de estados auxiliares

### 7.1 Evidencia de pago

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> REJECTED_DUPLICATE: misma imagen
    RECEIVED --> OCR_DONE: extracción
    OCR_DONE --> AUTO_CHECK_PASSED: todas las reglas OK
    OCR_DONE --> NEEDS_REVIEW: alguna regla falla
    OCR_DONE --> REJECTED_DUPLICATE: transacción ya usada
    AUTO_CHECK_PASSED --> APPROVED: humano con MFA
    NEEDS_REVIEW --> APPROVED: humano con MFA
    AUTO_CHECK_PASSED --> REJECTED_MISMATCH: humano
    NEEDS_REVIEW --> REJECTED_MISMATCH: humano
    NEEDS_REVIEW --> REJECTED_FRAUD: humano
    NEEDS_REVIEW --> REJECTED_OTHER: humano
    APPROVED --> [*]
    REJECTED_DUPLICATE --> [*]
    REJECTED_MISMATCH --> [*]
    REJECTED_FRAUD --> [*]
    REJECTED_OTHER --> [*]
```

### 7.2 Conversación

```mermaid
stateDiagram-v2
    [*] --> BOT: primer mensaje (aviso de IA)
    BOT --> HUMAN: handoff_to_human / el equipo toma / echo desde la app
    HUMAN --> BOT: el equipo devuelve al bot / vence bot_paused_until
    BOT --> CLOSED: inactividad prolongada
    HUMAN --> CLOSED: el equipo cierra
    CLOSED --> BOT: nuevo mensaje del cliente
```

### 7.3 Trabajo de la cola

```mermaid
stateDiagram-v2
    [*] --> QUEUED: enqueue (debounce)
    QUEUED --> RUNNING: claim_jobs (SKIP LOCKED)
    RUNNING --> DONE: finish_job(ok)
    RUNNING --> QUEUED: falla y quedan intentos (backoff)
    RUNNING --> QUEUED: lease vencido (reaper)
    RUNNING --> DEAD: falla con intentos agotados
    DEAD --> [*]
    DONE --> [*]
```

## 8. Diagrama de secuencia — agendar una cita con seña

Modela RF-05 y RF-06.

```mermaid
sequenceDiagram
    autonumber
    actor P as Paciente
    participant W as Worker (agente)
    participant L as LLM
    participant D as PostgreSQL
    participant B as PaymentProvider

    P->>W: Quiero una limpieza el martes en la mañana
    W->>L: turno con tools habilitadas por ClinicaPolicy
    L-->>W: check_availability(servicio, fecha)
    W->>D: available_slots() — horario del recurso menos citas activas
    D-->>W: 09:00, 09:30, 10:00
    W->>L: tool_result
    L-->>W: Tengo 09:00, 09:30 o 10:00, ¿cuál prefieres?
    W->>P: (mensaje interactivo con botones)
    P->>W: 09:30
    W->>L: turno
    L-->>W: hold_appointment(servicio, recurso, 09:30)
    W->>D: hold_appointment() — EXCLUDE garantiza que no haya solapamiento
    D-->>W: CITA-2026-000123, total 350,00, seña 105,00, vence en 15 min
    W->>B: create_charge(CITA-2026-000123, 105,00)
    B-->>W: QR dinámico
    W->>P: QR + "tu horario queda reservado 15 minutos"
    B->>D: confirmación del banco → payments (BANK_API)
    D->>D: trigger: seña cubierta → CONFIRMED
    W->>P: Cita confirmada (plantilla de utilidad para el recordatorio de 24 h antes)
```

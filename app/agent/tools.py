TOOLS: list[dict] = [
    {
        "name": "create_appointment",
        "description": (
            "Agenda una nueva cita para el usuario en sesión. "
            "Verifica disponibilidad automáticamente antes de confirmar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_type": {
                    "type": "string",
                    "description": "Tipo de servicio o especialidad médica",
                    "maxLength": 100,
                },
                "datetime_iso": {
                    "type": "string",
                    "description": "Fecha y hora en ISO 8601 con zona America/Bogota, ej: 2025-04-22T10:00:00-05:00",
                },
                "notes": {
                    "type": "string",
                    "description": "Observaciones opcionales del usuario",
                    "maxLength": 500,
                },
                "provider_id": {
                    "type": "string",
                    "description": "ID del proveedor/doctor (opcional)",
                    "maxLength": 100,
                },
            },
            "required": ["service_type", "datetime_iso"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cancel_appointment",
        "description": (
            "Cancela una cita existente. "
            "IMPORTANTE: solo llamar después de confirmación EXPLÍCITA del usuario."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "UUID de la cita a cancelar",
                },
                "reason": {
                    "type": "string",
                    "description": "Motivo de cancelación (opcional)",
                    "maxLength": 300,
                },
                "notify_user": {
                    "type": "boolean",
                    "description": "Enviar notificación al usuario",
                    "default": True,
                },
            },
            "required": ["appointment_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "reschedule_appointment",
        "description": "Reprograma una cita a nueva fecha/hora sin eliminar el registro original.",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "UUID de la cita a reprogramar",
                },
                "new_datetime_iso": {
                    "type": "string",
                    "description": "Nueva fecha y hora en ISO 8601",
                },
                "reason": {
                    "type": "string",
                    "description": "Motivo del cambio (opcional)",
                    "maxLength": 300,
                },
            },
            "required": ["appointment_id", "new_datetime_iso"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_appointments",
        "description": "Retorna las citas del usuario en sesión.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "cancelled", "completed", "all"],
                    "description": "Filtro por estado de la cita",
                },
                "limit": {
                    "type": "integer",
                    "description": "Número máximo de resultados",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "check_availability",
        "description": "Consulta slots disponibles en un rango de fechas para un servicio.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_type": {
                    "type": "string",
                    "description": "Tipo de servicio a consultar",
                },
                "date_from": {
                    "type": "string",
                    "description": "Fecha inicio en formato YYYY-MM-DD",
                },
                "date_to": {
                    "type": "string",
                    "description": "Fecha fin en formato YYYY-MM-DD (opcional, por defecto +7 días)",
                },
                "provider_id": {
                    "type": "string",
                    "description": "Filtrar por proveedor específico (opcional)",
                },
            },
            "required": ["service_type", "date_from"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_reminder",
        "description": "Envía un recordatorio inmediato de una cita por el canal indicado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "UUID de la cita",
                },
                "channel": {
                    "type": "string",
                    "enum": ["voice", "sms", "email", "whatsapp"],
                    "description": "Canal de envío del recordatorio",
                    "default": "sms",
                },
                "message_override": {
                    "type": "string",
                    "description": "Mensaje personalizado (opcional)",
                    "maxLength": 500,
                },
            },
            "required": ["appointment_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fill_web_form",
        "description": (
            "Abre un sitio web indicado y registra los datos del usuario en su "
            "formulario, escribiendo campo por campo en orden de tabulación. "
            "Solo funciona con sitios previamente autorizados."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL http/https del formulario a diligenciar",
                },
                "fecha": {
                    "type": "string",
                    "description": "Fecha en el formato que espera el sitio, ej: 2026-08-20",
                },
                "nombre": {"type": "string", "maxLength": 100},
                "apellido": {"type": "string", "maxLength": 100},
                "telefono": {"type": "string", "maxLength": 30},
                "direccion": {"type": "string", "maxLength": 200},
                "correo": {"type": "string", "maxLength": 150},
                "descripcion": {
                    "type": "string",
                    "description": "Descripción u observaciones (opcional)",
                    "maxLength": 500,
                },
            },
            "required": [
                "url",
                "fecha",
                "nombre",
                "apellido",
                "telefono",
                "direccion",
                "correo",
            ],
            "additionalProperties": False,
        },
    },
]

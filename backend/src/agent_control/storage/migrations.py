SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        external_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(channel, external_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT,
        channel TEXT NOT NULL,
        kind TEXT NOT NULL,
        sender_id TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        text TEXT,
        attachments_json TEXT NOT NULL,
        raw_json TEXT,
        correlation_id TEXT NOT NULL,
        received_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_memory (
        conversation_id TEXT PRIMARY KEY,
        summary TEXT NOT NULL,
        facts_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        objective TEXT NOT NULL,
        status TEXT NOT NULL,
        conversation_id TEXT,
        plan_id TEXT,
        current_step_id TEXT,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plans (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        objective TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subtasks (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        dependencies_json TEXT NOT NULL,
        retry_count INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_signals (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        signal TEXT NOT NULL,
        actor TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        summary TEXT NOT NULL,
        action_payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_invocations (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        capability TEXT NOT NULL,
        request_json TEXT NOT NULL,
        result_json TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        task_id TEXT,
        artifact_type TEXT NOT NULL,
        uri TEXT,
        content_preview TEXT,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        actor TEXT NOT NULL,
        task_id TEXT,
        correlation_id TEXT,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
]

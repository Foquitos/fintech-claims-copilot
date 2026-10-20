-- agent-api keeps its LangGraph checkpoints (paused cases) in its own database, apart from
-- core-mock's data. Postgres runs this only when the volume is created for the first time.
CREATE DATABASE agent;

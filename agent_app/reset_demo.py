from app.config import settings
from app.db import Base, engine


def main() -> int:
    if not settings.allow_destructive_agent_reset:
        print(
            "AP Agent database reset blocked. Set "
            "ALLOW_DESTRUCTIVE_AGENT_RESET=true to enable it explicitly."
        )
        return 1

    print(
        "AP Agent destructive reset enabled "
        f"(environment={settings.app_env}, "
        f"database={settings.database_backend})."
    )
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("AP Agent database reset completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

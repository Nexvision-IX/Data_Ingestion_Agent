from app.db import Base, engine


if __name__ == "__main__":

    Base.metadata.drop_all(
        bind=engine
    )

    Base.metadata.create_all(
        bind=engine
    )

    print(
        "AP Agent database reset completed."
    )
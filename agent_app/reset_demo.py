from app.db import Base, engine
from app.integrations.sap.mock import MockSAPGateway


if __name__ == "__main__":

    Base.metadata.drop_all(
        bind=engine
    )

    Base.metadata.create_all(
        bind=engine
    )

    MockSAPGateway().reset()

    print(
        "AP Agent database and mock SAP data reset."
    )

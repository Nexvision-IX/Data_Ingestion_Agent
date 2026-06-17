from app.integrations.posting.base import PostingGateway
from app.integrations.posting.mock import MockPostingGateway


def get_posting_gateway() -> PostingGateway:
    return MockPostingGateway()

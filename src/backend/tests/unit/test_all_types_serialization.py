import json
from fastapi.encoders import jsonable_encoder

import pytest

from langflow.interface.components import get_and_cache_all_types_dict
from langflow.services.deps import get_settings_service


@pytest.mark.asyncio
async def test_all_types_json_serializable():
    settings = get_settings_service()
    all_types = await get_and_cache_all_types_dict(settings)
    # jsonable_encoder should convert Enums/Pydantic to serializable types
    encoded = jsonable_encoder(all_types)
    # Ensure json.dumps doesn't raise
    json.dumps(encoded)
    # Also ensure it's a dict with components key or similar
    assert isinstance(encoded, dict)
    assert len(encoded) > 0

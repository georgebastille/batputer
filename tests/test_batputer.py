import json
from unittest.mock import patch

import batputer


def _fake_response(data: dict):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(data).encode()

    return FakeResponse()


def test_detect_context_window_found():
    data = {"data": [{"id": "gemma-4-26b-a4b-it-mlx", "loaded_context_length": 262144}]}
    with patch("urllib.request.urlopen", return_value=_fake_response(data)):
        assert batputer._detect_context_window("gemma-4-26b-a4b-it-mlx") == 262144


def test_detect_context_window_falls_back_to_max():
    data = {"data": [{"id": "gemma-4-26b-a4b-it-mlx", "max_context_length": 8192}]}
    with patch("urllib.request.urlopen", return_value=_fake_response(data)):
        assert batputer._detect_context_window("gemma-4-26b-a4b-it-mlx") == 8192


def test_detect_context_window_model_not_found():
    data = {"data": [{"id": "other-model", "loaded_context_length": 4096}]}
    with patch("urllib.request.urlopen", return_value=_fake_response(data)):
        assert batputer._detect_context_window("gemma-4-26b-a4b-it-mlx") is None


def test_detect_context_window_unreachable():
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        assert batputer._detect_context_window("gemma-4-26b-a4b-it-mlx") is None

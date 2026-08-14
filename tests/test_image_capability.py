from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

TYPES_PATH = Path(__file__).resolve().parents[1] / "core" / "shared" / "types.py"


def _load_types_module(name: str):
    spec = importlib.util.spec_from_file_location(name, TYPES_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ImageCapabilityReloadTests(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop("image_types_current", None)
        sys.modules.pop("image_types_previous", None)

    def test_capabilities_from_previous_module_generation_are_supported(self):
        current = _load_types_module("image_types_current")
        previous = _load_types_module("image_types_previous")
        capabilities = (
            previous.ImageCapability.TEXT_TO_IMAGE
            | previous.ImageCapability.IMAGE_TO_IMAGE
        )

        self.assertTrue(
            current.has_image_capability(
                capabilities,
                current.ImageCapability.IMAGE_TO_IMAGE,
            )
        )
        self.assertFalse(
            current.has_image_capability(
                capabilities,
                current.ImageCapability.RESOLUTION,
            )
        )

    def test_invalid_capability_value_is_not_supported(self):
        current = _load_types_module("image_types_current")

        self.assertFalse(
            current.has_image_capability(
                "image-to-image",
                current.ImageCapability.IMAGE_TO_IMAGE,
            )
        )

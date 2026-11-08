"""Host integration contracts; no Forge checkout, GPU or model downloads needed."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strata import compat


class Memory(unittest.TestCase):
    def run_adapter(self, loaded, modules):
        def load(name):
            if name not in modules:
                raise ImportError(name)
            return modules[name]
        with patch.dict(sys.modules, loaded), patch.object(compat.importlib, "import_module", side_effect=load):
            return compat.free_graphics_memory()

    def test_modern_forge_and_neo(self):
        manager = Mock()
        self.assertTrue(self.run_adapter({}, {"backend.memory_management": manager}))
        manager.unload_all_models.assert_called_once_with()
        manager.soft_empty_cache.assert_called_once_with()

    def test_legacy_forge(self):
        manager = Mock()
        self.assertTrue(self.run_adapter({}, {"ldm_patched.modules.model_management": manager}))
        manager.unload_all_models.assert_called_once_with()
        manager.soft_empty_cache.assert_called_once_with()

    def test_loaded_legacy_wins_over_an_unused_modern_backend(self):
        legacy, modern = Mock(), Mock()
        modules = {"ldm_patched.modules.model_management": legacy, "backend.memory_management": modern}
        self.assertTrue(self.run_adapter({"ldm_patched.modules.model_management": legacy}, modules))
        legacy.unload_all_models.assert_called_once_with()
        modern.unload_all_models.assert_not_called()

    def test_both_loaded_are_both_freed(self):
        # an older Forge with Forge 2 files: its checkpoint is held by ldm_patched while backend is imported too;
        # freeing only backend left the checkpoint on the card and BiRefNet ran out of memory (2026-10-20)
        legacy, modern = Mock(), Mock()
        modules = {"ldm_patched.modules.model_management": legacy, "backend.memory_management": modern}
        self.assertTrue(self.run_adapter(dict(modules), modules))
        legacy.unload_all_models.assert_called_once_with()
        modern.unload_all_models.assert_called_once_with()
        legacy.soft_empty_cache.assert_called_once_with()

    def test_unavailable_host_is_nonfatal(self):
        with patch("builtins.print"):
            self.assertFalse(self.run_adapter({}, {}))

    def test_broken_manager_falls_back(self):
        broken, legacy = Mock(), Mock()
        broken.unload_all_models.side_effect = RuntimeError("unavailable device")
        self.assertTrue(self.run_adapter({}, {"backend.memory_management": broken,
                                              "ldm_patched.modules.model_management": legacy}))
        legacy.soft_empty_cache.assert_called_once_with()


class Deforum(unittest.TestCase):
    def test_active_extension_can_have_a_custom_folder_name(self):
        with tempfile.TemporaryDirectory() as folder:
            os.mkdir(os.path.join(folder, "scripts"))
            with open(os.path.join(folder, "scripts", "deforum_api.py"), "w"):
                pass
            registry = types.SimpleNamespace(active=lambda: [types.SimpleNamespace(path=folder)])
            host = types.ModuleType("modules")
            host.extensions = registry
            with patch.dict(sys.modules, {"modules": host}):
                self.assertEqual(compat.deforum_dir(), folder)

    def test_disabled_extension_is_not_reported_as_available(self):
        host = types.ModuleType("modules")
        host.extensions = types.SimpleNamespace(active=lambda: [])
        with patch.dict(sys.modules, {"modules": host}):
            self.assertIsNone(compat.deforum_dir())


if __name__ == "__main__":
    unittest.main()

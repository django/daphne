import sys
from pathlib import Path


def test_fd_endpoint_plugin_installed():
    # Find the site-packages directory
    for path in sys.path:
        if "site-packages" in path:
            site_packages = Path(path)
            break
    else:
        raise AssertionError("Could not find site-packages in sys.path")

    plugin_path = site_packages / "twisted" / "plugins" / "fd_endpoint.py"
    assert plugin_path.exists(), f"fd_endpoint.py not found at {plugin_path}"

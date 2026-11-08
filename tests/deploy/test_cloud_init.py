import json
import re

import yaml

from .conftest import ROOT

TEXT = (ROOT / "deploy" / "cloud-init.yaml").read_text()
CFG = yaml.safe_load(TEXT)


def _file(path):
    return next(f for f in CFG["write_files"] if f["path"] == path)["content"]


def test_header_and_ssh_hardening():
    assert TEXT.splitlines()[0] == "#cloud-config"
    assert CFG["ssh_pwauth"] is False
    assert CFG["disable_root"] is True
    dropin = _file("/etc/ssh/sshd_config.d/60-unitpricer.conf")
    for line in ("PasswordAuthentication no", "KbdInteractiveAuthentication no",
                 "PermitRootLogin no", "AllowUsers deploy"):
        assert line in dropin.splitlines()


def test_deploy_user_and_swap():
    user = next(u for u in CFG["users"] if isinstance(u, dict) and u["name"] == "deploy")
    assert user["sudo"] == "ALL=(ALL) NOPASSWD:ALL"
    assert user["shell"] == "/bin/bash"
    assert CFG["swap"] == {"filename": "/swapfile", "size": "1G"}


def test_docker_log_rotation_and_reboot_window():
    daemon = json.loads(_file("/etc/docker/daemon.json"))
    assert daemon["log-opts"] == {"max-size": "10m", "max-file": "5"}
    apt = _file("/etc/apt/apt.conf.d/52unitpricer-upgrades")
    assert 'Unattended-Upgrade::Automatic-Reboot "true";' in apt
    assert 'Unattended-Upgrade::Automatic-Reboot-Time "08:00";' in apt


def test_firewall_order_and_docker_install():
    cmds = [c if isinstance(c, str) else " ".join(c) for c in CFG["runcmd"]]
    joined = "\n".join(cmds)
    assert joined.index("ufw default deny incoming") < joined.index("ufw --force enable")
    assert "ufw allow OpenSSH" in joined
    for pkg in ("docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"):
        assert pkg in joined
    assert "usermod -aG docker deploy" in joined
    assert "/srv/unitpricer" in joined


def test_no_secrets():
    assert not re.search(r"(?i)token|password=|secret", TEXT)

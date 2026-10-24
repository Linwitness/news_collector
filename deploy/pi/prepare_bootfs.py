"""
prepare_bootfs.py — Write the first-boot config onto a freshly flashed Raspberry Pi OS card.

Usage (on the Mac, after flashing Raspberry Pi OS Lite 64-bit; boot partition mounted):
    WIFI_SSID=... WIFI_PASSWORD=... python3 deploy/pi/prepare_bootfs.py /Volumes/bootfs BUNDLE

Writes cloud-init `user-data` (hostname newspi, user lin with the Mac's SSH key,
key-only SSH, timezone) and `network-config` (Ethernet + Wi-Fi), and copies the
setup script, its systemd unit and the bundle (see deploy/pi/setup.sh). The Wi-Fi
password comes from the environment and is written only to the card.
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOSTNAME = "newspi"
USER = "lin"
TIMEZONE = "America/Los_Angeles"
SSH_KEY = os.path.expanduser("~/.ssh/id_rsa.pub")


def user_data(ssh_key: str) -> str:
    return f"""#cloud-config
hostname: {HOSTNAME}
manage_etc_hosts: true
timezone: {TIMEZONE}
keyboard:
  model: pc105
  layout: us
# The Pi has no battery-backed clock: don't let a wrong date block apt before NTP syncs.
apt:
  conf: |
    Acquire {{
      Check-Date "false";
    }};
users:
- name: {USER}
  groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo
  shell: /bin/bash
  lock_passwd: true
  sudo: ALL=(ALL) NOPASSWD:ALL
  ssh_authorized_keys:
  - {json.dumps(ssh_key)}
ssh_pwauth: false
runcmd:
- [systemctl, enable, --now, ssh]
- [cp, /boot/firmware/newspi-setup.service, /etc/systemd/system/newspi-setup.service]
- [systemctl, daemon-reload]
- [systemctl, enable, newspi-setup.service]
- [systemctl, start, --no-block, newspi-setup.service]
"""


def network_config(ssid: str, password: str) -> str:
    return f"""network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
  wifis:
    wlan0:
      dhcp4: true
      optional: true
      regulatory-domain: US
      access-points:
        {json.dumps(ssid)}:
          password: {json.dumps(password)}
"""


def main():
    bootfs, bundle = sys.argv[1], sys.argv[2]
    ssid, password = os.environ["WIFI_SSID"], os.environ["WIFI_PASSWORD"]
    with open(SSH_KEY) as f:
        ssh_key = f.read().strip()
    with open(os.path.join(bootfs, "user-data"), "w") as f:
        f.write(user_data(ssh_key))
    with open(os.path.join(bootfs, "network-config"), "w") as f:
        f.write(network_config(ssid, password))
    open(os.path.join(bootfs, "ssh"), "w").close()  # legacy switch: enable sshd on boot
    shutil.copy(os.path.join(HERE, "setup.sh"), os.path.join(bootfs, "newspi-setup.sh"))
    shutil.copy(os.path.join(HERE, "newspi-setup.service"), bootfs)
    shutil.copy(bundle, os.path.join(bootfs, "newspi-bundle.tar.gz"))
    print(f"Wrote user-data, network-config (Wi-Fi: {ssid}), setup files and bundle to {bootfs}.")


if __name__ == "__main__":
    main()

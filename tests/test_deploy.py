"""Tests for the OmniParser deploy helpers (no AWS or SSH access needed)."""

import socket
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")
pytest.importorskip("paramiko")

from botocore.exceptions import ClientError

from openadapt_grounding.deploy import deploy
from openadapt_grounding.deploy.deploy import Deploy, TunnelProcess


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class TestLocalizeTimestamps:
    CEST = timezone(timedelta(hours=2))

    def test_converts_docker_utc_timestamp(self):
        out = deploy._localize_timestamps(
            "2026-07-01T10:00:00.123456789Z server started", tz=self.CEST
        )
        assert out == "2026-07-01 12:00:00 +0200 server started"

    @pytest.mark.parametrize("ts", ["2026-07-01T10:00:00.5Z", "2026-07-01T10:00:00Z"])
    def test_trimmed_or_missing_fraction(self, ts):
        out = deploy._localize_timestamps(f"{ts} x", tz=self.CEST)
        assert out == "2026-07-01 12:00:00 +0200 x"

    def test_defaults_to_local_timezone(self):
        out = deploy._localize_timestamps("2026-07-01T10:00:00.5Z x")
        expected = datetime(2026, 7, 1, 10, tzinfo=timezone.utc).astimezone()
        assert out == expected.strftime("%Y-%m-%d %H:%M:%S %z") + " x"

    def test_lines_without_timestamp_unchanged(self):
        text = "start parsing...\nimage size: (1200, 779)"
        assert deploy._localize_timestamps(text) == text


def test_free_local_port_is_bindable():
    port = deploy._free_local_port()
    with socket.socket() as s:
        s.bind(("localhost", port))


class TestTunnelProcess:
    def test_context_manager_returns_url_and_stops(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        with TunnelProcess(proc, "http://localhost:9000") as url:
            assert url == "http://localhost:9000"
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()

    def test_stop_is_noop_when_already_exited(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        TunnelProcess(proc, "http://localhost:9000").stop()
        proc.terminate.assert_not_called()


class TestTunnel:
    @pytest.fixture(autouse=True)
    def _instance(self, monkeypatch, tmp_path):
        key = tmp_path / "omniparser.pem"
        key.write_text("dummy")
        monkeypatch.setattr(Deploy, "_get_instance_ip", staticmethod(lambda: "203.0.113.5"))
        monkeypatch.setattr(
            type(deploy.config), "AWS_EC2_KEY_PATH", property(lambda self: str(key))
        )

    def test_forwards_local_port_over_ssh(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        with patch.object(deploy.subprocess, "Popen", return_value=proc) as popen, patch.object(
            deploy.socket, "create_connection", MagicMock()
        ):
            tunnel = Deploy.tunnel(local_port=9001)

        cmd = popen.call_args.args[0]
        assert cmd[0] == "ssh"
        assert f"9001:localhost:{deploy.config.PORT}" in cmd
        assert "ExitOnForwardFailure=yes" in cmd
        assert cmd[-1] == f"{deploy.config.AWS_EC2_USER}@203.0.113.5"
        assert isinstance(tunnel, TunnelProcess)
        assert tunnel.local_url == "http://localhost:9001"

    def test_raises_when_ssh_exits(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 255
        with (
            patch.object(deploy.subprocess, "Popen", return_value=proc),
            pytest.raises(RuntimeError, match="exited unexpectedly"),
        ):
            Deploy.tunnel(local_port=9001)

    def test_raises_without_running_instance(self, monkeypatch):
        monkeypatch.setattr(Deploy, "_get_instance_ip", staticmethod(lambda: None))
        with pytest.raises(RuntimeError, match="No running instance"):
            Deploy.tunnel(local_port=9001)


class TestSecure:
    def _ec2(self):
        ec2 = MagicMock()
        ec2.describe_security_groups.return_value = {"SecurityGroups": [{"GroupId": "sg-123"}]}
        return ec2

    def test_revokes_public_api_port(self):
        ec2 = self._ec2()
        with patch.object(deploy.boto3, "client", return_value=ec2):
            Deploy.secure()
        perm = ec2.revoke_security_group_ingress.call_args.kwargs["IpPermissions"][0]
        assert perm["FromPort"] == perm["ToPort"] == deploy.config.PORT
        assert perm["IpRanges"] == [{"CidrIp": "0.0.0.0/0"}]

    def test_already_secure_is_not_an_error(self, capsys):
        ec2 = self._ec2()
        ec2.revoke_security_group_ingress.side_effect = _client_error("InvalidPermission.NotFound")
        with patch.object(deploy.boto3, "client", return_value=ec2):
            Deploy.secure()
        assert "already secure" in capsys.readouterr().out

    def test_missing_security_group_is_not_an_error(self):
        ec2 = MagicMock()
        ec2.describe_security_groups.side_effect = _client_error("InvalidGroup.NotFound")
        with patch.object(deploy.boto3, "client", return_value=ec2):
            Deploy.secure()
        ec2.revoke_security_group_ingress.assert_not_called()


def test_new_security_groups_only_open_ssh():
    ec2 = MagicMock()
    ec2.describe_security_groups.side_effect = _client_error("InvalidGroup.NotFound")
    ec2.create_security_group.return_value = {"GroupId": "sg-new"}
    with patch.object(deploy.boto3, "client", return_value=ec2):
        deploy.get_or_create_security_group_id()
    perms = ec2.authorize_security_group_ingress.call_args.kwargs["IpPermissions"]
    assert [p["FromPort"] for p in perms] == [22]

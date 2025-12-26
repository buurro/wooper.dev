"""Tests for main.py FastAPI endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from packaging.version import Version

from wooper_dev.actual_logic import NixpkgsRev, Package
from wooper_dev.main import MAX_PACKAGES, app

client = TestClient(app)


@pytest.fixture
def mock_package():
    """Create a mock package for testing."""
    return Package(
        name="python3",
        version=Version("3.11.0"),
        nixpkgs_rev=NixpkgsRev(
            rev="abc123def456",
            hash="sha256-xxxyyyzzz",
            date=1700000000,
        ),
        _input_name="n0",
    )


class TestFlakeEndpoint:
    def test_returns_flake_nix(self, mock_package):
        with patch(
            "wooper_dev.main.packages_from_string",
            new_callable=AsyncMock,
            return_value=[mock_package],
        ):
            response = client.get("/flake/python3")

        assert response.status_code == 200
        assert "quickshell" in response.text
        assert "mkPackages" in response.text
        assert "python3" in response.text

    def test_rejects_too_many_packages(self):
        packages = ";".join([f"pkg{i}" for i in range(MAX_PACKAGES + 1)])
        response = client.get(f"/flake/{packages}")

        assert response.status_code == 400
        assert "Too many packages" in response.json()["detail"]

    def test_returns_404_for_missing_package(self):
        with patch(
            "wooper_dev.main.packages_from_string",
            new_callable=AsyncMock,
            side_effect=ValueError("Version not found for package nonexistent"),
        ):
            response = client.get("/flake/nonexistent")

        assert response.status_code == 404

    def test_returns_503_on_db_failure(self):
        from psycopg.errors import ConnectionFailure

        with patch(
            "wooper_dev.main.packages_from_string",
            new_callable=AsyncMock,
            side_effect=ConnectionFailure("Database unavailable"),
        ):
            response = client.get("/flake/python3")

        assert response.status_code == 503
        assert "Database unavailable" in response.json()["detail"]


class TestTarballEndpoint:
    def test_returns_tarball(self, mock_package):
        import io

        with (
            patch("wooper_dev.main.packages_from_string", new_callable=AsyncMock, return_value=[mock_package]),
            patch("wooper_dev.main.get_flake_tarball", return_value=io.BytesIO(b"fake tarball")),
        ):
            response = client.get("/python3")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/gzip"
        assert "attachment" in response.headers["content-disposition"]

    def test_rejects_too_many_packages(self):
        packages = ";".join([f"pkg{i}" for i in range(MAX_PACKAGES + 1)])
        response = client.get(f"/{packages}")

        assert response.status_code == 400

    def test_returns_404_for_missing_package(self):
        with patch(
            "wooper_dev.main.packages_from_string",
            new_callable=AsyncMock,
            side_effect=ValueError("Version not found for package nonexistent"),
        ):
            response = client.get("/nonexistent")

        assert response.status_code == 404


class TestNixpkgsEndpoint:
    def test_redirects_to_github(self, mock_package):
        with patch(
            "wooper_dev.main.get_package",
            new_callable=AsyncMock,
            return_value=mock_package,
        ):
            response = client.get("/nixpkgs/python3", follow_redirects=False)

        assert response.status_code == 301
        assert "github.com/NixOS/nixpkgs/archive" in response.headers["location"]
        assert "abc123def456" in response.headers["location"]

    def test_returns_404_for_missing_package(self):
        with patch(
            "wooper_dev.main.get_package",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get("/nixpkgs/nonexistent")

        assert response.status_code == 404

    def test_returns_400_for_invalid_requirement(self):
        response = client.get("/nixpkgs/invalid[[[requirement")
        assert response.status_code == 400
        assert "Invalid requirement" in response.json()["detail"]


class TestRevEndpoint:
    def test_returns_rev_info(self, mock_package):
        with patch(
            "wooper_dev.main.get_package",
            new_callable=AsyncMock,
            return_value=mock_package,
        ):
            response = client.get("/api/rev/python3")

        assert response.status_code == 200
        data = response.json()
        assert data["rev"] == "abc123def456"
        assert data["hash"] == "sha256-xxxyyyzzz"
        assert data["date"] == 1700000000

    def test_returns_404_for_missing_package(self):
        with patch(
            "wooper_dev.main.get_package",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get("/api/rev/nonexistent")

        assert response.status_code == 404


class TestStatsEndpoint:
    def test_returns_revs_per_day(self):
        with patch(
            "wooper_dev.main.get_revs_per_day",
            new_callable=AsyncMock,
            return_value=[
                {"date": "2024-01-15", "count": 5},
                {"date": "2024-01-14", "count": 3},
            ],
        ):
            response = client.get("/api/stats/revs-per-day")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["date"] == "2024-01-15"
        assert data[0]["count"] == 5

    def test_returns_503_on_db_failure(self):
        from psycopg.errors import ConnectionFailure

        with patch(
            "wooper_dev.main.get_revs_per_day",
            new_callable=AsyncMock,
            side_effect=ConnectionFailure("Database unavailable"),
        ):
            response = client.get("/api/stats/revs-per-day")

        assert response.status_code == 503


class TestInputValidation:
    def test_accepts_version_specifier(self, mock_package):
        with patch(
            "wooper_dev.main.get_package",
            new_callable=AsyncMock,
            return_value=mock_package,
        ):
            response = client.get("/api/rev/python3>=3.10")

        assert response.status_code == 200

    def test_accepts_multiple_packages(self, mock_package):
        with patch(
            "wooper_dev.main.packages_from_string",
            new_callable=AsyncMock,
            return_value=[mock_package, mock_package],
        ):
            response = client.get("/flake/python3;nodejs")

        assert response.status_code == 200

    def test_max_packages_boundary(self, mock_package):
        """Test exactly MAX_PACKAGES is allowed."""
        packages = ";".join([f"pkg{i}" for i in range(MAX_PACKAGES)])
        with patch(
            "wooper_dev.main.packages_from_string",
            new_callable=AsyncMock,
            return_value=[mock_package] * MAX_PACKAGES,
        ):
            response = client.get(f"/flake/{packages}")

        assert response.status_code == 200

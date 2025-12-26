"""Tests for actual_logic.py"""

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from wooper_dev.actual_logic import (
    AMBIGUOUS_PACKAGES,
    NixpkgsRev,
    Package,
    check_ambiguous,
    get_flake_lock,
    get_flake_nix,
    select_optimal_packages,
)


class TestNixpkgsRev:
    def test_comparison(self):
        rev1 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        rev2 = NixpkgsRev(rev="def456", hash="sha256-yyy", date=2000)

        assert rev2 > rev1
        assert not rev1 > rev2

    def test_hash(self):
        rev1 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        rev2 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)

        assert hash(rev1) == hash(rev2)
        assert rev1 == rev2

    def test_can_be_used_in_set(self):
        rev1 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        rev2 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        rev3 = NixpkgsRev(rev="def456", hash="sha256-yyy", date=2000)

        s = {rev1, rev2, rev3}
        assert len(s) == 2


class TestPackage:
    def test_input_name_default(self):
        rev = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        pkg = Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev)

        assert pkg.input_name == "n-python3"

    def test_input_name_custom(self):
        rev = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        pkg = Package(
            name="python3",
            version=Version("3.11.0"),
            nixpkgs_rev=rev,
            input_name_override="n0",
        )

        assert pkg.input_name == "n0"

    def test_comparison_by_version(self):
        rev = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        pkg1 = Package(name="python3", version=Version("3.10.0"), nixpkgs_rev=rev)
        pkg2 = Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev)

        assert pkg2 > pkg1

    def test_comparison_by_rev_when_same_version(self):
        rev1 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        rev2 = NixpkgsRev(rev="def456", hash="sha256-yyy", date=2000)
        pkg1 = Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev1)
        pkg2 = Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev2)

        assert pkg2 > pkg1


class TestSelectOptimalPackages:
    def test_single_package(self):
        rev = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        candidates = {
            "python3": [
                Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev),
            ]
        }
        requirements = [Requirement("python3")]

        result = select_optimal_packages(requirements, candidates)

        assert len(result) == 1
        assert result[0].name == "python3"
        assert result[0].version == Version("3.11.0")

    def test_selects_max_version(self):
        rev = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        candidates = {
            "python3": [
                Package(name="python3", version=Version("3.10.0"), nixpkgs_rev=rev),
                Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev),
                Package(name="python3", version=Version("3.9.0"), nixpkgs_rev=rev),
            ]
        }
        requirements = [Requirement("python3")]

        result = select_optimal_packages(requirements, candidates)

        assert result[0].version == Version("3.11.0")

    def test_consolidates_revisions(self):
        """When multiple packages can use the same revision, they should share it."""
        rev1 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        rev2 = NixpkgsRev(rev="def456", hash="sha256-yyy", date=2000)

        candidates = {
            "python3": [
                Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev1),
                Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev2),
            ],
            "nodejs": [
                Package(name="nodejs", version=Version("20.0.0"), nixpkgs_rev=rev1),
                Package(name="nodejs", version=Version("20.0.0"), nixpkgs_rev=rev2),
            ],
        }
        requirements = [Requirement("python3"), Requirement("nodejs")]

        result = select_optimal_packages(requirements, candidates)

        # Both packages should share the same input_name (same revision)
        assert result[0].input_name == result[1].input_name

    def test_uses_minimum_revisions(self):
        """Should use minimum number of revisions to cover all packages."""
        rev1 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        rev2 = NixpkgsRev(rev="def456", hash="sha256-yyy", date=2000)
        rev3 = NixpkgsRev(rev="ghi789", hash="sha256-zzz", date=3000)

        # python3 v3.11 only in rev1, nodejs v20 only in rev2, git v2.40 in both
        candidates = {
            "python3": [
                Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev1),
            ],
            "nodejs": [
                Package(name="nodejs", version=Version("20.0.0"), nixpkgs_rev=rev2),
            ],
            "git": [
                Package(name="git", version=Version("2.40.0"), nixpkgs_rev=rev1),
                Package(name="git", version=Version("2.40.0"), nixpkgs_rev=rev2),
                Package(name="git", version=Version("2.40.0"), nixpkgs_rev=rev3),
            ],
        }
        requirements = [
            Requirement("python3"),
            Requirement("nodejs"),
            Requirement("git"),
        ]

        result = select_optimal_packages(requirements, candidates)

        # Should use only 2 revisions (rev1 for python3+git, rev2 for nodejs)
        # or (rev1 for python3, rev2 for nodejs+git)
        input_names = {pkg.input_name for pkg in result}
        assert len(input_names) == 2

    def test_raises_on_missing_package(self):
        candidates = {"python3": []}
        requirements = [Requirement("python3")]

        with pytest.raises(ValueError, match="Version not found"):
            select_optimal_packages(requirements, candidates)

    def test_raises_on_duplicate_package(self):
        rev = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        candidates = {
            "python3": [
                Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev),
            ]
        }
        requirements = [Requirement("python3"), Requirement("python3")]

        with pytest.raises(ValueError, match="Duplicate package: python3"):
            select_optimal_packages(requirements, candidates)


class TestGetFlakeNix:
    def test_generates_valid_flake(self):
        rev = NixpkgsRev(rev="abc123def456", hash="sha256-xxx", date=1000)
        packages = [
            Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev, input_name_override="n0"),
        ]

        flake_nix = get_flake_nix(packages)

        assert "quickshell" in flake_nix
        assert "mkPackages" in flake_nix
        assert "n0" in flake_nix
        assert "abc123def456" in flake_nix
        assert "python3" in flake_nix

    def test_deduplicates_inputs(self):
        rev = NixpkgsRev(rev="abc123def456", hash="sha256-xxx", date=1000)
        packages = [
            Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev, input_name_override="n0"),
            Package(name="nodejs", version=Version("20.0.0"), nixpkgs_rev=rev, input_name_override="n0"),
        ]

        flake_nix = get_flake_nix(packages)

        assert flake_nix.count("n0.url") == 1
        assert "python3" in flake_nix
        assert "nodejs" in flake_nix

    def test_groups_packages_by_input(self):
        rev1 = NixpkgsRev(rev="abc123", hash="sha256-xxx", date=1000)
        rev2 = NixpkgsRev(rev="def456", hash="sha256-yyy", date=2000)
        packages = [
            Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev1, input_name_override="n0"),
            Package(name="nodejs", version=Version("20.0.0"), nixpkgs_rev=rev2, input_name_override="n1"),
        ]

        flake_nix = get_flake_nix(packages)

        assert "n0" in flake_nix
        assert "n1" in flake_nix


class TestGetFlakeLock:
    def test_generates_valid_lock(self):
        import json

        rev = NixpkgsRev(rev="abc123def456", hash="sha256-xxx", date=1000)
        packages = [
            Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev, input_name_override="n0"),
        ]

        lock_data = json.loads(get_flake_lock(packages))

        assert "quickshell" in lock_data["nodes"]
        assert "n0" in lock_data["nodes"]
        assert lock_data["nodes"]["n0"]["locked"]["rev"] == "abc123def456"

    def test_deduplicates_inputs(self):
        import json

        rev = NixpkgsRev(rev="abc123def456", hash="sha256-xxx", date=1000)
        packages = [
            Package(name="python3", version=Version("3.11.0"), nixpkgs_rev=rev, input_name_override="n0"),
            Package(name="nodejs", version=Version("20.0.0"), nixpkgs_rev=rev, input_name_override="n0"),
        ]

        lock_data = json.loads(get_flake_lock(packages))

        assert len(lock_data["nodes"]) == 3  # root, quickshell, n0


class TestCheckAmbiguous:
    def test_ambiguous_package_raises(self):
        """Ambiguous packages like python should raise an error."""
        req = Requirement("python")

        with pytest.raises(ValueError, match="Ambiguous package"):
            check_ambiguous(req)

    def test_ambiguous_package_with_specifier_raises(self):
        """Ambiguous packages should raise even with version specifiers."""
        req = Requirement("python>=3.0")

        with pytest.raises(ValueError, match="Ambiguous package"):
            check_ambiguous(req)

    def test_ambiguous_error_message_has_guidance(self):
        """Error message should tell user what to do."""
        req = Requirement("python")

        with pytest.raises(ValueError, match="python2.*python3"):
            check_ambiguous(req)

    def test_non_ambiguous_passes(self):
        """Non-ambiguous packages should not raise."""
        req = Requirement("python3>=3.11")
        check_ambiguous(req)  # Should not raise

    def test_python_is_ambiguous(self):
        """Verify python is marked as ambiguous."""
        assert "python" in AMBIGUOUS_PACKAGES
        assert "python2" in AMBIGUOUS_PACKAGES["python"]
        assert "python3" in AMBIGUOUS_PACKAGES["python"]

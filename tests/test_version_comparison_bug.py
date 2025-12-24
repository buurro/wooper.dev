"""
Tests for version comparison behavior.

SQL max(version) uses lexicographic comparison, not semantic versioning.
This means "9.0" > "10.0" lexicographically, causing wrong version selection.

Our app avoids this by doing version comparison in Python.
"""

import os

import psycopg
import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from wooper_dev.actual_logic import get_package

# Skip if no database connection
pytestmark = pytest.mark.skipif(
    not os.getenv("WOOPER_DB"),
    reason="WOOPER_DB not set",
)


@pytest.fixture
def db_connection():
    """Create a database connection."""
    connection_info = os.getenv("WOOPER_DB")
    assert connection_info is not None
    conn = psycopg.connect(connection_info)
    yield conn
    conn.close()


@pytest.fixture
def test_data(db_connection):
    """Insert test data with versions that expose the lexicographic bug."""
    cursor = db_connection.cursor()

    # Insert versions that will fail lexicographic comparison
    # "9.0" > "10.0" > "11.0" lexicographically (9 > 1)
    # Each version needs its own revision (unique constraint is on rev+package)
    test_versions = ["9.0", "10.0", "11.0"]
    for i, version in enumerate(test_versions):
        rev_name = f"test_rev_version_bug_{i}"
        cursor.execute(
            """
            INSERT INTO revs (rev, hash, date)
            VALUES (%s, 'sha256-test', %s)
            ON CONFLICT (rev) DO NOTHING
            """,
            (rev_name, 1000 + i),
        )
        cursor.execute(
            """
            INSERT INTO packages (rev, package, version)
            VALUES (%s, 'test_version_bug_pkg', %s)
            ON CONFLICT (rev, package) DO NOTHING
            """,
            (rev_name, version),
        )

    db_connection.commit()

    yield test_versions

    # Cleanup
    cursor.execute("DELETE FROM packages WHERE package = 'test_version_bug_pkg'")
    cursor.execute("DELETE FROM revs WHERE rev LIKE 'test_rev_version_bug_%'")
    db_connection.commit()
    cursor.close()


class TestVersionComparison:
    """Tests for version comparison behavior."""

    def test_sql_max_is_lexicographic(self, db_connection, test_data):
        """
        Demonstrates that SQL max(version) is lexicographic, not semantic.
        This is why we do version comparison in Python.
        """
        cursor = db_connection.cursor()
        cursor.execute("""
            SELECT max(version)
            FROM packages
            WHERE package = 'test_version_bug_pkg'
        """)
        sql_max = cursor.fetchone()[0]
        cursor.close()

        # SQL returns "9.0" (lexicographically largest)
        assert sql_max == "9.0"

        # But semantic max is "11.0"
        versions = [Version(v) for v in test_data]
        semantic_max = str(max(versions))
        assert semantic_max == "11.0"

        # They differ - this is why we can't use SQL max()
        assert sql_max != semantic_max

    @pytest.mark.asyncio
    async def test_app_returns_correct_semantic_max(self, test_data):
        """
        Verify our app correctly returns the semantic max version,
        not the lexicographic max.
        """
        requirement = Requirement("test_version_bug_pkg")
        package = await get_package(requirement)

        assert package is not None
        # App should return 11.0 (semantic max), not 9.0 (lexicographic max)
        assert package.version == Version("11.0")

    def test_lexicographic_vs_semantic_comparison(self):
        """Demonstrate the difference between lexicographic and semantic comparison."""
        versions = ["9.0", "10.0", "11.0", "2.0", "1.10"]

        # Lexicographic max (what SQL does) - WRONG
        lexicographic_max = max(versions)

        # Semantic max (what we want) - CORRECT
        semantic_max = str(max(Version(v) for v in versions))

        # These are different!
        assert lexicographic_max == "9.0"  # Wrong answer
        assert semantic_max == "11.0"  # Correct answer
        assert lexicographic_max != semantic_max

    def test_more_version_edge_cases(self):
        """More examples where lexicographic comparison fails."""
        test_cases = [
            # (versions, expected_semantic_max, wrong_lexicographic_max)
            (["1.9", "1.10"], "1.10", "1.9"),
            (["2.0", "10.0"], "10.0", "2.0"),
            (["0.9.0", "0.10.0", "0.11.0"], "0.11.0", "0.9.0"),
            (["1.2.3", "1.2.10"], "1.2.10", "1.2.3"),
        ]

        for versions, expected_semantic, expected_lexicographic in test_cases:
            semantic_max = str(max(Version(v) for v in versions))
            lexicographic_max = max(versions)

            assert semantic_max == expected_semantic
            assert lexicographic_max == expected_lexicographic
            assert semantic_max != lexicographic_max, (
                f"For {versions}: lexicographic gives '{lexicographic_max}' "
                f"but semantic should give '{expected_semantic}'"
            )

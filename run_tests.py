# run_tests.py
import pytest
import sys

if __name__ == "__main__":
    # Run tests with verbose output
    args = ["-v", "--tb=short", "-x"]  # Stop on first failure

    # Add integration tests if requested
    if "--run-integration" in sys.argv:
        args.append("-m")
        args.append("integration")
        sys.argv.remove("--run-integration")

    exit_code = pytest.main(args + ["tests/"])
    sys.exit(exit_code)

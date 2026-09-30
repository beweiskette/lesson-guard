import pytest

from lesson_guard.paths import glob_match, normalize


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("*.py", "src/a.py", True),
        ("*.py", "a.py", True),
        ("*.py", "src/a.pyc", False),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/sub/a.py", False),
        ("src/**/*.py", "src/a.py", True),
        ("src/**/*.py", "src/x/y/a.py", True),
        ("**/backup*/**", "backup_old/a.gd", True),
        ("**/backup*/**", "game/backups/x/a.gd", True),
        ("**/backup*/**", "game/scripts/a.gd", False),
        (".env", "deploy/.env", True),
        (".env.*", ".env.example", True),
        (".env", ".env.example", False),
        ("file?.txt", "file1.txt", True),
        ("file[0-9].txt", "file7.txt", True),
        ("file[!0-9].txt", "file7.txt", False),
    ],
)
def test_glob_match(pattern, path, expected):
    assert glob_match(pattern, path) is expected


def test_normalize_relative_to_cwd():
    assert normalize(r"C:\work\proj\src\a.py", r"C:\work\proj") == "src/a.py"
    assert normalize("/home/u/proj/src/a.py", "/home/u/proj/") == "src/a.py"
    assert normalize("./src/a.py") == "src/a.py"
    assert normalize("/elsewhere/a.py", "/home/u/proj") == "/elsewhere/a.py"

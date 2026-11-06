"""Regular expressions the viewer tests use to check that no file path reaches the data or the page. The home folder
part is joined from pieces so that the repository's own scan for private paths (qa.judge_scan) does not read these
patterns as a path."""

_HOME = "/" + "Users" + "/"  # macOS home folders
# in JSON text (backslashes escaped)
IN_JSON = r"[A-Za-z]:\\\\|" + _HOME + r"|/home/|\\\\Temp\\\\"
IN_JSON_SHORT = r"[A-Za-z]:\\\\|" + _HOME + r"|/home/"
# in the page's text
ON_PAGE = r"[A-Za-z]:\\|" + _HOME + r"|/home/"

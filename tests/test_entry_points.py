import sys
import unittest
from unittest.mock import patch

from reel import cli


class EntryPointTests(unittest.TestCase):
    def test_reel_mcp_is_reel_mcp_subcommand(self):
        # `uvx reel-mcp film.mp4 --until 8` must reach the same parser as `reel mcp film.mp4 --until 8`.
        with patch.object(sys, "argv", ["reel-mcp", "film.mp4", "--until", "8"]), patch.object(cli, "main") as main:
            cli.mcp_main()
            self.assertEqual(sys.argv, ["reel-mcp", "mcp", "film.mp4", "--until", "8"])
        main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

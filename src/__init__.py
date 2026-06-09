import click


@click.group(context_settings={'help_option_names': ['-h', '--help']})
def fetch():
    """fetch CLI."""


@fetch.command()
def mcp():
    """Run the MCP server (stdio) exposing the invoice tools to agents."""
    from src.mcp_server import main  # noqa: PLC0415

    main()


import src.onboarding  # noqa: E402, F401

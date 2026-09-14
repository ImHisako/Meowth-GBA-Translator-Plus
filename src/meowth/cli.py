"""Meowth CLI - GBA Pokemon translation tool."""

from pathlib import Path

import click

from .core import TranslationCallbacks, TranslationConfig, TranslationEngine
from .languages import validate_language
from .pipeline import Pipeline
from .translator import PROVIDER_PRESETS


def _load_env():
    """Load .env file if present."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                import os
                value = val.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ.setdefault(key.strip(), value)


def _load_config() -> dict:
    """Load meowth.toml config if present."""
    config_path = Path(__file__).parent.parent.parent / "meowth.toml"
    if not config_path.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib.loads(config_path.read_text(encoding="utf-8"))


def _provider_kwargs(provider, api_base, api_key_env, model) -> dict:
    """Build provider kwargs from CLI options, falling back to meowth.toml."""
    cfg = _load_config()
    t = cfg.get("translation", {})
    api_cfg = t.get("api", {})

    return {
        "provider": provider or t.get("provider"),
        "api_base": api_base or (api_cfg.get("base_url") if not provider or provider == t.get("provider") else None),
        "api_key_env": api_key_env or (api_cfg.get("key_env") if not provider or provider == t.get("provider") else None),
        "model": model or (t.get("model") if not provider or provider == t.get("provider") else None),
    }


def _get_language(cli_value, cli_default, config_key) -> str:
    """Use TOML only when the language option was not explicitly supplied."""
    cfg = _load_config()
    t = cfg.get("translation", {})
    # None is distinct from explicitly selecting the default language.
    if cli_value is not None:
        return cli_value
    return t.get(config_key, cli_default)


# Shared CLI options for LLM provider configuration
_provider_options = [
    click.option("--provider", default=None, type=click.Choice(sorted(PROVIDER_PRESETS.keys()), case_sensitive=False),
                 help="Translation provider (e.g. deepl, openai, deepseek)"),
    click.option("--api-base", default=None, help="Custom API base URL (include /v2 for DeepL)"),
    click.option("--api-key-env", default=None, help="Environment variable name for API key (DeepL: comma-separated keys supported)"),
    click.option("--model", default=None, help="Model name to use"),
]


def add_provider_options(func):
    """Decorator to add all provider options to a click command."""
    for option in reversed(_provider_options):
        func = option(func)
    return func


class CLICallbacks(TranslationCallbacks):
    """CLI implementation of translation callbacks."""

    def on_log(self, level: str, message: str):
        """Output log messages to the terminal."""
        if level == "error":
            click.secho(message, fg="red", err=True)
        elif level == "warning":
            click.secho(message, fg="yellow")
        else:
            click.echo(message)

    def on_progress(self, stage: str, current: int, total: int, message: str):
        """Output progress updates to the terminal."""
        # Progress is already included in log messages
        pass

    def on_stage_change(self, stage: str, status: str):
        """Handle stage changes (not needed for CLI)."""
        pass

    def on_error(self, error: Exception):
        """Output errors to the terminal."""
        click.secho(f"Error: {error}", fg="red", err=True)


@click.group()
def main():
    """Meowth - GBA Pokemon ROM translation tool."""
    _load_env()


@main.command()
@click.argument("rom_path", type=click.Path(exists=True))
@click.option("-o", "--output", default="work/texts.json", help="Output texts JSON path")
@click.option("--source", default=None, help="Source language code (default: from config or en)")
@click.option("--target", default=None, help="Target language code (default: from config or zh-Hans)")
def extract(rom_path, output, source, target):
    """Extract texts from ROM using MeowthBridge."""
    source = _get_language(source, "en", "source_language")
    target = _get_language(target, "zh-Hans", "target_language")
    validate_language(source)
    validate_language(target)
    TranslationEngine.extract_texts(Path(rom_path), Path(output))
    click.echo(f"Extracted: {output}")


@main.command()
@click.argument("texts_json", type=click.Path(exists=True))
@click.option("-o", "--output", default="work/texts_translated.json")
@click.option("--batch-size", default=None, type=click.IntRange(min=1), help="Texts per batch")
@click.option("--workers", default=None, type=click.IntRange(min=1), help="Parallel translation threads")
@click.option("--source", default=None, help="Source language code (default: from config or en)")
@click.option("--target", default=None, help="Target language code (default: from config or zh-Hans)")
@add_provider_options
def translate(texts_json, output, batch_size, workers, source, target,
              provider, api_base, api_key_env, model):
    """Translate extracted texts JSON via DeepL or an LLM API."""
    source = _get_language(source, "en", "source_language")
    target = _get_language(target, "zh-Hans", "target_language")
    validate_language(source)
    validate_language(target)
    kwargs = _provider_kwargs(provider, api_base, api_key_env, model)

    config = TranslationConfig(
        source_lang=source,
        target_lang=target,
        batch_size=batch_size if batch_size is not None else _load_config().get("translation", {}).get("batch_size", 30),
        max_workers=workers if workers is not None else _load_config().get("translation", {}).get("max_workers", 10),
        **kwargs
    )
    engine = TranslationEngine(config, CLICallbacks())
    try:
        engine.translate_texts(Path(texts_json), Path(output))
    finally:
        engine.close()
    click.echo(f"Translated: {output}")


@main.command()
@click.argument("rom_path", type=click.Path(exists=True))
@click.option("--translations", required=True, type=click.Path(exists=True))
@click.option("-o", "--output", required=True)
@click.option("--source", default=None, help="Source language code (default: from config or en)")
@click.option("--target", default=None, help="Target language code (default: from config or zh-Hans)")
def build(rom_path, translations, output, source, target):
    """Build translated ROM from translations."""
    source = _get_language(source, "en", "source_language")
    target = _get_language(target, "zh-Hans", "target_language")
    validate_language(source)
    validate_language(target)

    config = TranslationConfig(source_lang=source, target_lang=target)
    engine = TranslationEngine(config, CLICallbacks())
    try:
        engine.build_rom(Path(rom_path), Path(translations), Path(output))
    finally:
        engine.close()


@main.command()
@click.argument("rom_path", type=click.Path(exists=True))
@click.option("-o", "--output-dir", default="outputs")
@click.option("--work-dir", default="work")
@click.option("--source", default=None, help="Source language code (default: from config or en)")
@click.option("--target", default=None, help="Target language code (default: from config or zh-Hans)")
@add_provider_options
def full(rom_path, output_dir, work_dir, source, target,
         provider, api_base, api_key_env, model):
    """Run full pipeline: extract -> translate -> build ROM."""
    source = _get_language(source, "en", "source_language")
    target = _get_language(target, "zh-Hans", "target_language")
    validate_language(source)
    validate_language(target)
    kwargs = _provider_kwargs(provider, api_base, api_key_env, model)

    config = TranslationConfig(
        source_lang=source,
        target_lang=target,
        rom_path=Path(rom_path),
        output_dir=Path(output_dir),
        work_dir=Path(work_dir),
        batch_size=_load_config().get("translation", {}).get("batch_size", 30),
        max_workers=_load_config().get("translation", {}).get("max_workers", 10),
        **kwargs
    )
    engine = TranslationEngine(config, CLICallbacks())
    try:
        engine.run_full()
    finally:
        engine.close()


if __name__ == "__main__":
    main()

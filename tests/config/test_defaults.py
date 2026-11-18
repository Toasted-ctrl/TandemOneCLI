import pytest

from tandemonecli.config.defaults import (
    Defaults,
    DefaultsError,
    load_defaults,
    save_defaults,
)


def test_missing_file_gives_empty_defaults(defaults_file):
    assert not defaults_file.exists()

    assert load_defaults() == Defaults()


def test_saved_defaults_are_loaded_again(defaults_file):
    saved = Defaults(provider="openai", model="gpt-a", web=True)

    save_defaults(saved)

    assert defaults_file.exists()
    assert load_defaults() == saved
    # The temporary file is gone once the file is replaced
    assert list(defaults_file.parent.iterdir()) == [defaults_file]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"provider": "openai", "colour": "red"}',
        '{"web": "maybe"}',
    ],
)
def test_invalid_file_raises_readable_error(defaults_file, text):
    defaults_file.parent.mkdir(parents=True)
    defaults_file.write_text(text, encoding="utf-8")

    with pytest.raises(DefaultsError, match="isn't valid"):
        load_defaults()


def test_max_output_tokens_saved_by_earlier_versions_are_ignored(defaults_file):
    defaults_file.parent.mkdir(parents=True)
    defaults_file.write_text(
        '{"provider": "openai", "model": "gpt-a", "max_output_tokens": 500000}',
        encoding="utf-8",
    )

    assert load_defaults() == Defaults(provider="openai", model="gpt-a")


def test_unwritable_location_raises_readable_error(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    with pytest.raises(DefaultsError, match="can't save"):
        save_defaults(Defaults(), blocker / "defaults.json")

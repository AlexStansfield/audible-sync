import json

from audible_sync import openapi_export


def test_the_schema_lists_the_api_without_a_server_or_database(tmp_path, monkeypatch):
    monkeypatch.setattr("audible_sync.database.DB_FILE", str(tmp_path / "never-created.db"))
    out = tmp_path / "openapi.json"

    openapi_export.main([str(out)])

    schema = json.loads(out.read_text())
    assert schema["info"]["version"] == openapi_export.SCHEMA_VERSION
    assert "/api/status" in schema["paths"]
    assert "BookOut" in schema["components"]["schemas"]
    assert not (tmp_path / "never-created.db").exists()


def test_the_output_is_stable_so_ci_can_diff_it(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    openapi_export.main([str(first)])
    openapi_export.main([str(second)])

    assert first.read_text() == second.read_text()

from anvil_serving.workbench_app.service import WorkbenchService


def test_document_catalog_is_fixed_and_body_has_source_and_content_digest(tmp_path):
    service = WorkbenchService({"state_path": str(tmp_path / "private.sqlite")}, None, {})
    try:
        catalog = service.read("documents", {}, None)["items"]
        assert {row["id"] for row in catalog} == {"serving", "anvil", "workbench", "benchmarks", "connect"}
        for row in catalog:
            document = service.read("documents/" + row["id"], {}, None)
            assert document["markdown"] and document["version"]
            assert len(document["digest"]) == 64
            assert document["source_url"].startswith("https://github.com/fakoli/")
    finally:
        service.close()

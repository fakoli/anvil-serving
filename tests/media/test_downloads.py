import datetime as dt
import urllib.parse


from anvil_serving.media.downloads import ArtifactDownloadLinks


def test_read_links_bind_identity_expiry_and_rotating_secret():
    clock = [1000]
    links = ArtifactDownloadLinks("synthetic-key", "owner-a", "https://media.test", clock=lambda: clock[0])
    artifact = {"id": "art_owned", "resource": "/artifacts/art_owned",
                "expiresAt": dt.datetime.fromtimestamp(2000, dt.timezone.utc).isoformat()}
    issued = links.issue(artifact); query = urllib.parse.urlsplit(issued["downloadUrl"]).query
    assert issued["requiresAuthentication"] is False and "synthetic-key" not in issued["downloadUrl"]
    assert links.permits("art_owned", query)
    assert not links.permits("art_other", query)
    assert not links.permits("art_owned", query + "&expires=2000")
    assert not links.permits("art_owned", query + "&extra=true")
    assert not ArtifactDownloadLinks("rotated", "owner-a", "https://media.test", clock=lambda: clock[0]).permits("art_owned", query)
    assert not ArtifactDownloadLinks("synthetic-key", "owner-b", "https://media.test", clock=lambda: clock[0]).permits("art_owned", query)
    clock[0] = 2000
    assert not links.permits("art_owned", query)

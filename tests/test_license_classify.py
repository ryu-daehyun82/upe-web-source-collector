"""license_state.auto_classify 단위테스트 (외부 의존 없음)."""
from app.models.enums import LicenseStatus
from app.policy.license_state import auto_classify


def test_cc_by_is_allowed():
    meta = {"license_url": "https://creativecommons.org/licenses/by/4.0/"}
    assert auto_classify(meta) == LicenseStatus.allowed


def test_cc0_public_domain_allowed():
    meta = {"license": "https://creativecommons.org/publicdomain/zero/1.0/"}
    assert auto_classify(meta) == LicenseStatus.allowed


def test_cc_by_nc_is_conditional():
    meta = {"license_href": "https://creativecommons.org/licenses/by-nc/4.0/"}
    assert auto_classify(meta) == LicenseStatus.conditional


def test_cc_by_nd_is_conditional():
    meta = {"license": "creativecommons.org/licenses/by-nd/4.0"}
    assert auto_classify(meta) == LicenseStatus.conditional


def test_spdx_mit_allowed():
    assert auto_classify({"spdx": "MIT"}) == LicenseStatus.allowed


def test_spdx_apache_allowed():
    assert auto_classify({"spdx_id": "Apache-2.0"}) == LicenseStatus.allowed


def test_spdx_gpl_conditional():
    assert auto_classify({"spdx": "GPL-3.0"}) == LicenseStatus.conditional


def test_rel_license_link_list():
    meta = {
        "links": [
            {"rel": "stylesheet", "href": "/a.css"},
            {"rel": "license", "href": "https://creativecommons.org/licenses/by/4.0/"},
        ]
    }
    assert auto_classify(meta) == LicenseStatus.allowed


def test_meta_name_license_in_html():
    meta = {"html": '<meta name="license" content="MIT">'}
    assert auto_classify(meta) == LicenseStatus.allowed


def test_all_rights_reserved_conditional():
    assert auto_classify({"copyright": "© 2026 Foo. All rights reserved."}) == (
        LicenseStatus.conditional
    )


def test_public_domain_phrase_allowed():
    assert auto_classify({"rights": "This work is in the public domain."}) == (
        LicenseStatus.allowed
    )


def test_no_clue_is_unknown():
    assert auto_classify({}) == LicenseStatus.unknown
    assert auto_classify({"title": "Some page"}) == LicenseStatus.unknown


def test_non_dict_is_unknown():
    assert auto_classify("MIT") == LicenseStatus.unknown  # type: ignore[arg-type]

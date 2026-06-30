"""RBAC 역할 기반 접근제어 (설계 §13.3).

역할: source_operator · license_reviewer · privacy_reviewer · pattern_reviewer · admin · auditor.
권한 원칙: raw snapshot 접근 최소화(admin) / pattern feature는 reviewer 이상 /
delete 처리는 admin·privacy / audit log는 auditor·admin. FastAPI 의존성 require()로 강제.
헤더 X-Upe-Actor / X-Upe-Roles 로 principal 구성.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, Header, HTTPException


class Role(str, Enum):
    """역할."""
    source_operator = "source_operator"
    license_reviewer = "license_reviewer"
    privacy_reviewer = "privacy_reviewer"
    pattern_reviewer = "pattern_reviewer"
    admin = "admin"
    auditor = "auditor"


class Permission(str, Enum):
    """권한."""
    source_register = "source_register"
    policy_check = "policy_check"
    crawl_job_create = "crawl_job_create"
    pattern_view_feature = "pattern_view_feature"
    pattern_approve = "pattern_approve"
    pattern_block = "pattern_block"
    delete_request = "delete_request"
    delete_apply = "delete_apply"
    recheck = "recheck"
    audit_view = "audit_view"
    snapshot_access = "snapshot_access"


_ALL = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.admin: _ALL,
    Role.source_operator: frozenset({
        Permission.source_register, Permission.policy_check,
        Permission.crawl_job_create, Permission.delete_request, Permission.recheck,
    }),
    Role.license_reviewer: frozenset({Permission.policy_check, Permission.pattern_view_feature}),
    Role.privacy_reviewer: frozenset({
        Permission.pattern_view_feature, Permission.delete_request, Permission.delete_apply,
    }),
    Role.pattern_reviewer: frozenset({
        Permission.pattern_view_feature, Permission.pattern_approve, Permission.pattern_block,
    }),
    Role.auditor: frozenset({Permission.audit_view, Permission.pattern_view_feature}),
}
# snapshot_access 는 admin(_ALL)만 보유 — raw snapshot 접근 최소화(§13.3).


@dataclass(frozen=True)
class Principal:
    """인증 주체(불변). actor_id + 역할 집합."""
    actor_id: str | None
    roles: frozenset[Role]

    def has(self, perm: Permission) -> bool:
        """어느 역할이든 perm 보유면 True(admin 은 전체)."""
        return any(perm in ROLE_PERMISSIONS.get(r, frozenset()) for r in self.roles)


def parse_roles(raw: str | None) -> frozenset[Role]:
    """콤마분리 역할 문자열 → frozenset[Role]. 공백 strip, 빈/미지 역할 무시."""
    if raw is None:
        return frozenset()
    result = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            result.add(Role(token))
        except ValueError:
            continue  # 알 수 없는 역할 무시
    return frozenset(result)


def has_permission(roles, permission: Permission) -> bool:
    """roles(iterable[Role]) 중 하나라도 permission 보유면 True."""
    return any(permission in ROLE_PERMISSIONS.get(r, frozenset()) for r in roles)


def get_principal(
    x_upe_actor: str | None = Header(default=None),
    x_upe_roles: str | None = Header(default=None),
) -> Principal:
    """FastAPI 의존성: 헤더 X-Upe-Actor / X-Upe-Roles 에서 Principal 구성."""
    return Principal(actor_id=x_upe_actor, roles=parse_roles(x_upe_roles))


def require(permission: Permission):
    """permission 보유를 강제하는 FastAPI 의존성 팩토리. 미보유면 403."""
    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(permission):
            raise HTTPException(status_code=403, detail=f"forbidden: requires '{permission.value}'")
        return principal

    return _dep
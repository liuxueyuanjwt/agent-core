# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""删除数量防护（Pipeline D）子包。"""

from openjiuwen.harness.security.permission_engine.delguard.del_count import (
    count_deletion_targets,
)
from openjiuwen.harness.security.permission_engine.delguard.deletion_guard import (
    DeletionGuard,
    SoftDeleteEnabledProvider,
    build_deletion_guard,
    default_soft_delete_enabled,
)

__all__ = [
    "DeletionGuard",
    "SoftDeleteEnabledProvider",
    "build_deletion_guard",
    "default_soft_delete_enabled",
    "count_deletion_targets",
]
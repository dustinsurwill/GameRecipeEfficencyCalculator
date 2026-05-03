# Central import point — ensures all SQLModel table classes are registered
# in SQLModel.metadata before Alembic or create_all runs.
# Import order matters for FK resolution.

from app.models.user import User, OAuthAccount                  # noqa: F401
from app.models.versioning import (                             # noqa: F401
    Game, GameVersion, Mod, ModVersion, ModPack, ModPackModVersion,
)
from app.models.identity import (                               # noqa: F401
    ItemIdentity, ItemAppearance,
    MachineIdentity, MachineUpgradeItem, MachineUpgradeModifier,
)
from app.models.status import RecipeStatus, PublicationStatus, RecipeReview  # noqa: F401
from app.models.recipe import (                                 # noqa: F401
    Recipe, RecipeSlot, RecipeModVersionLink, RecipeOverride,
)
from app.models.admin import AdminGroup                         # noqa: F401
from app.auth.refresh import RefreshToken                       # noqa: F401

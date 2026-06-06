export interface User {
	id: string;
	display_name: string;
	email: string | null;
	email_verified: boolean;
	is_active: boolean;
	is_superuser: boolean;
	is_curator: boolean;
	bio: string | null;
	avatar_url: string | null;
	created_at: string;
	updated_at: string;
}

export interface OAuthAccount {
	id: string;
	user_id: string;
	provider: string;
	provider_email: string | null;
	last_used_at: string | null;
}

export interface UserWithAccounts extends User {
	oauth_accounts: OAuthAccount[];
}

export interface Game {
	id: string;
	name: string;
	slug: string;
	recipe_metric: string;
	icon_url: string | null;
}

export interface GameVersion {
	id: string;
	game_id: string;
	version: string;
	is_latest: boolean;
	released_at: string | null;
}

export interface Mod {
	id: string;
	game_id: string;
	name: string;
	slug: string;
	namespace: string;
	source_url: string | null;
	description: string | null;
	icon_url: string | null;
}

export interface ModVersion {
	id: string;
	mod_id: string;
	game_version_id: string;
	version: string;
	status: 'draft' | 'published' | 'deprecated' | 'archived';
	created_at: string;
}

export interface ModPackIdentity {
	id: string;
	game_id: string;
	name: string;
	slug: string;
	source_url: string | null;
	description: string | null;
	icon_url: string | null;
}

export interface ModPackVersion {
	id: string;
	identity_id: string;
	game_version_id: string;
	name: string;
	slug: string;
	version: string;
	status: 'draft' | 'published' | 'deprecated' | 'archived';
	icon_url: string | null;
	source_url: string | null;
	description: string | null;
}

export interface AdminGroup {
	id: string;
	user_id: string;
	scope_type: 'mod' | 'modpack';
	scope_id: string;
	role: 'admin' | 'maintainer';
}

export interface Recipe {
	id: string;
	import_key: string;
	recipe_type: string;
	machine_identity_id: string | null;
	modpack_version_id: string | null;
	status: 'draft' | 'pending_review' | 'approved' | 'rejected';
	source_priority: number;
	contributor_id: string | null;
	created_at: string;
}

export interface RecipeSlot {
	id: string;
	recipe_id: string;
	slot_type: 'input' | 'output' | 'aux_input';
	item_appearance_id: string | null;
	tag_string: string | null;
	quantity: number;
	slot_index: number;
}

export interface RecipeReview {
	id: string;
	recipe_id: string;
	reviewer_id: string;
	decision: 'approved' | 'rejected';
	reason: string | null;
	previous_status: string;
	created_at: string;
}

// ── Chain types (matches /chains response) ────────────────────────────────────

export interface ChainScopeModpack {
	type: 'modpack';
	modpack_identity_id: string;
	modpack_version_id: string;
	modpack_name: string;
	modpack_version: string;
}

export interface ChainScopeMods {
	type: 'mods';
	mods: { mod: string; version: string; mod_version_id: string }[];
	game_version_id?: string;
}

export interface ChainScopeGame {
	type: 'game';
	game_id: string;
	game_version_id: string;
}

export type ChainScope = ChainScopeModpack | ChainScopeMods | ChainScopeGame;

export interface ChainSlot {
	tag: string | null;
	item_id: string | null;
	canonical_name?: string | null;
	quantity: number;
	is_fluid: boolean;
}

export interface ChainMachine {
	id: string;
	name: string;
	recipe_type: string;
	mod_namespace: string;
}

export interface ChainStep {
	step: number;
	machine: ChainMachine;
	recipe_id: string;
	inputs: ChainSlot[];
	outputs: ChainSlot[];
	aux_inputs: ChainSlot[];
	time_ticks: number | null;
	energy_rf: number | null;
}

export interface Chain {
	rank: number;
	yield_per_start_item: number | null;
	raw_materials_per_target: number | null;
	has_cycle: boolean;
	cycle_at_step: number | null;
	raw_inputs: ChainSlot[];
	final_outputs: { item_id: string; canonical_name: string | null; quantity: number }[];
	steps: ChainStep[];
	machines_required: ChainMachine[];
	chemicals_required: string[];
}

export interface ChainResponse {
	target: string;
	start: string | null;
	direction: 'forward' | 'backward';
	scope: ChainScope;
	quantity: number;
	total_found: number;
	returned: number;
	cycles_found: number;
	cycles_excluded: number;
	excluded_applied: string[];
	chains: Chain[];
	note?: string;
}

export interface PaginatedResponse<T> {
	items: T[];
	total: number;
	limit: number;
	offset: number;
}

export interface MachineIdentity {
	id: string;
	game_id: string;
	mod_id: string | null;
	name: string;
	recipe_types_json: string;
	is_time_based: boolean;
	icon_url: string | null;
}

export interface ItemIdentity {
	id: string;
	canonical_name: string;
	canonical_namespace: string | null;
	game_id: string;
	notes: string | null;
	icon_url: string | null;
}

export interface ItemAppearance {
	id: string;
	identity_id: string;
	mod_version_id: string;
	item_id: string;
	namespace: string;
	tags_json: string;
}

export interface ItemAppearanceWithIdentity {
	appearance: ItemAppearance;
	identity: ItemIdentity;
}

export interface ModPackModEntry {
	mod: Mod;
	mod_version: ModVersion;
}

export interface TokenClaims {
	sub: string;
	type: string;
	curator: boolean;
	superuser: boolean;
	exp: number;
	iat: number;
}

export interface ItemIdentityRead {
	id: string;
	canonical_name: string;
	canonical_namespace: string | null;
	game_id: string;
	notes: string | null;
	icon_url: string | null;
}

export interface RecipeSlotInfo {
	item_id: string | null;
	tag_string: string | null;
	quantity: number;
	is_fluid: boolean;
	fluid_label: string | null;
}

export interface RecipeInfo {
	id: string;
	recipe_type: string;
	mod_namespace: string;
	machine_name: string;
	inputs: RecipeSlotInfo[];
	outputs: RecipeSlotInfo[];
	aux_inputs: RecipeSlotInfo[];
}

export interface ItemInfoRead {
	item_id: string;
	produces: RecipeInfo[];
	uses: RecipeInfo[];
}

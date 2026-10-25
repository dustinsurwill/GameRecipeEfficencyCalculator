import { api } from './api';

export interface DItem {
	item_id: string;
	name: string;
	namespace: string;
	mod_name: string;
	tags: string[];
	made_by: number;
	used_in: number;
}

export interface DMod {
	namespace: string;
	name: string;
	description: string | null;
	item_count: number;
	recipe_count: number;
}

export interface DMachine {
	id: string;
	name: string;
	namespace: string;
	mod_name: string;
	recipe_types: string[];
	recipe_count: number;
}

export interface DTag {
	id: string;
	member_count: number;
	recipe_count: number;
}

export interface DSlot {
	item_id: string | null;
	name: string;
	tag: string | null;
	quantity: number;
	is_fluid: boolean;
	fluid_label: string | null;
	via_tag: boolean;
}

export interface DRecipe {
	id: string;
	recipe_type: string;
	namespace: string;
	machine: { id: string; name: string };
	inputs: DSlot[];
	outputs: DSlot[];
	aux_inputs: DSlot[];
	time_ticks: number | null;
	energy_rf: number | null;
	modpack_override: boolean;
}

export interface Paged<T> {
	total: number;
	limit: number;
	offset: number;
	items: T[];
}

export interface Facet {
	id: string;
	name: string;
	namespace: string;
	count: number;
}


export interface ItemDetail extends Omit<DItem, 'tags'> {
	tags: { id: string; member_count: number }[];
	machines: { made_in: Facet[]; used_in: Facet[] };
}

export interface Overview {
	counts: { items: number; recipes: number; mods: number; machines: number; tags: number };
	top_mods: DMod[];
	top_machines: DMachine[];
	popular_items: DItem[];
}

export interface SearchResults {
	items: DItem[];
	mods: DMod[];
	machines: DMachine[];
	tags: DTag[];
}

const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
	const sp = new URLSearchParams();
	for (const [k, v] of Object.entries(params)) {
		if (v !== null && v !== undefined && v !== '' && v !== false) sp.set(k, String(v));
	}
	const s = sp.toString();
	return s ? `?${s}` : '';
};

export const discover = {
	overview: () => api.get<Overview>('/discover/overview'),
	search: (q: string) => api.get<SearchResults>(`/discover/search${qs({ q })}`),
	items: (p: Record<string, string | number | boolean | null | undefined>) =>
		api.get<Paged<DItem>>(`/discover/items${qs(p)}`),
	item: (id: string) => api.get<ItemDetail>(`/discover/item${qs({ id })}`),
	recipes: (p: Record<string, string | number | boolean | null | undefined>) =>
		api.get<Paged<DRecipe>>(`/discover/recipes${qs(p)}`),
	mods: (p: Record<string, string | number | undefined> = {}) =>
		api.get<Paged<DMod>>(`/discover/mods${qs(p)}`),
	mod: (ns: string) => api.get<DMod & { machines: DMachine[] }>(`/discover/mods/${encodeURIComponent(ns)}`),
	machines: (p: Record<string, string | undefined> = {}) =>
		api.get<{ total: number; items: DMachine[] }>(`/discover/machines${qs(p)}`),
	machine: (id: string) => api.get<DMachine>(`/discover/machines/${id}`),
	tag: (id: string) => api.get<DTag & { members: DItem[] }>(`/discover/tag${qs({ id })}`)
};

export const itemHref = (id: string) => `/items/${encodeURIComponent(id)}`;
export const tagHref = (id: string) => `/tag?id=${encodeURIComponent(id)}`;

export const fmtNum = (n: number) => new Intl.NumberFormat('en').format(n);
export const fmtQty = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, ''));

/** "c:ingots/iron" -> "Iron Ingots", "minecraft:planks" -> "Planks", "mekanism:foo/bar" -> "Bar Foo (mekanism)" */
export function tagLabel(tag: string): string {
	const [ns, path = ''] = tag.includes(':') ? tag.split(':', 2) : ['', tag];
	const words = path.split('/').reverse().join(' ').replace(/_/g, ' ');
	const title = words.replace(/\b\w/g, (c) => c.toUpperCase());
	return ns === 'c' || ns === 'minecraft' || !ns ? title : `${title} (${ns})`;
}

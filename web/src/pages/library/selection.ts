/**
 * The Library's bulk selection (DESIGN.md, Library: bulk actions). Either some books
 * picked by id, which survives paging and filtering, or every book the filters match,
 * which is sent as the filters rather than the ids and so ends when they change.
 */
export type Selection = { all: false; ids: ReadonlySet<number> } | { all: true; total: number };

export type SelectionAction =
  /** One book's checkbox; `pageIds` are the books on show, what "all matching" narrows to */
  | { type: "toggle"; id: number; pageIds: readonly number[] }
  /** The page's checkbox: every id on the page in, or every one out */
  | { type: "page"; ids: readonly number[]; on: boolean }
  | { type: "all"; total: number }
  | { type: "clear" }
  /** The filters changed: "all matching" meant the old ones */
  | { type: "filters" };

export const EMPTY: Selection = { all: false, ids: new Set() };

export function selectionReducer(state: Selection, action: SelectionAction): Selection {
  switch (action.type) {
    case "toggle": {
      // Unticking one book out of "all matching": the other pages' ids were never
      // loaded, so what is left is the rest of this page
      if (state.all) return { all: false, ids: new Set(action.pageIds.filter((id) => id !== action.id)) };
      const ids = new Set(state.ids);
      if (ids.has(action.id)) ids.delete(action.id);
      else ids.add(action.id);
      return { all: false, ids };
    }
    case "page": {
      if (state.all) return action.on ? state : EMPTY;
      const ids = new Set(state.ids);
      for (const id of action.ids) {
        if (action.on) ids.add(id);
        else ids.delete(id);
      }
      return { all: false, ids };
    }
    case "all":
      return { all: true, total: action.total };
    case "clear":
      return EMPTY;
    case "filters":
      return state.all ? EMPTY : state;
  }
}

export function selectedCount(selection: Selection): number {
  return selection.all ? selection.total : selection.ids.size;
}

export function isSelected(selection: Selection, id: number): boolean {
  return selection.all || selection.ids.has(id);
}

/** Whether every book on the page is in the selection (the table's header checkbox). */
export function pageSelected(selection: Selection, ids: readonly number[]): boolean {
  return ids.length > 0 && ids.every((id) => isSelected(selection, id));
}

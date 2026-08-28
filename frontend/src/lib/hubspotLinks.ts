const OBJECT_TYPE_IDS = {
  contact: "0-1",
  company: "0-2",
  deal: "0-3",
  ticket: "0-5",
} as const;

export type HsObjectType = keyof typeof OBJECT_TYPE_IDS;

export const recordUrl = (
  portalId: string | number,
  type: HsObjectType,
  id: string
): string =>
  `https://app.hubspot.com/contacts/${portalId}/record/${OBJECT_TYPE_IDS[type]}/${id}`;

export const listUrl = (
  portalId: string | number,
  listId: string | number
): string => `https://app.hubspot.com/contacts/${portalId}/lists/${listId}`;

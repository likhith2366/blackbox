// Demo catalog (static list). checkout-svc and payment-svc expose no catalog,
// inventory or pricing endpoint. The SKUs are the ones the demo system already
// sends (demo/loadgen.py ITEMS); names and prices are display values defined here
// only so the page can compute the `amount` field of POST /orders.
// There is no stock level: the backend does not track inventory.

export interface Product {
  sku: string
  name: string
  price: number // USD, display only
}

export const CATALOG: Product[] = [
  { sku: 'sku-1001', name: 'Canvas tote bag', price: 18.5 },
  { sku: 'sku-1002', name: 'Ceramic mug', price: 12.0 },
  { sku: 'sku-2040', name: 'Desk lamp', price: 46.9 },
  { sku: 'sku-3310', name: 'Wool throw', price: 79.0 },
  { sku: 'sku-4420', name: 'Wireless headphones', price: 149.0 },
  { sku: 'sku-5005', name: 'Espresso grinder', price: 229.0 },
  { sku: 'sku-7781', name: 'Standing desk frame', price: 349.0 },
]

export const MAX_QTY = 9

export const money = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })

export const lineAmount = (price: number, qty: number) => Math.round(price * qty * 100) / 100

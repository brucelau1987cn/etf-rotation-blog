import { handleBaoStockInternal } from './_handler.js';

export async function onRequest(context) {
  return handleBaoStockInternal(context);
}

export async function onRequestGet(context) {
  return handleBaoStockInternal(context);
}

export async function onRequestOptions(context) {
  return handleBaoStockInternal(context);
}

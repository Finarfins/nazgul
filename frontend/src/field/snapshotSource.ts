import {api} from '../api';

import {createFieldSyncController} from './sessionCoordinator';
import {applySnapshot,captureWriteEpoch} from './fieldDatabase';

const FIELD_READ_ENDPOINTS = new Set(['/field/snapshot']);

export async function syncFieldSnapshot(companyId: number, userId: number): Promise<boolean> {
  const expectedEpoch=await captureWriteEpoch();
  const {controller,release}=createFieldSyncController();
  const endpoint = '/field/snapshot';
  try{
    if (!FIELD_READ_ENDPOINTS.has(endpoint)) throw new Error('Saha API yüzeyi allow-list dışında');
    const {data} = await api.get(endpoint,{signal:controller.signal});
    return await applySnapshot(data, companyId, userId, expectedEpoch);
  }finally{release()}
}

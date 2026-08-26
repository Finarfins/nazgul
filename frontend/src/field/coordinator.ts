import {closeFieldDatabase} from './fieldDatabase';

const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('sungur-field-db-v1');
channel?.addEventListener('message', event => {
  if (event.data?.type === 'prepare-upgrade') {
    closeFieldDatabase();
    channel.postMessage({type: 'upgrade-ready'});
  }
});

export function announceFieldDbUpgrade() {
  channel?.postMessage({type: 'prepare-upgrade'});
}

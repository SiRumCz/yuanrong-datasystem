# Datasystem

The ubiquitous language of `yuanrong-datasystem` — a high-performance, highly-available distributed cache and data
infrastructure. It exposes object, key-value, stream, and heterogeneous-device caches to clients, served by a cluster of
worker processes coordinated by a master over a consistent hash ring. This glossary is derived from the code (public
headers under `include/datasystem`, protos under `src/datasystem/protos`, and `src/datasystem` source). Terms are the
ones a contributor is likely to misread or to name inconsistently; general programming concepts (TTL, LRU, versioning,
RPC plumbing, thread pools) are deliberately excluded.

## Topology and processes

**Worker**:
The datasystem runtime process running on a node; it holds cached objects and streams, owns a span of the hash ring, and
serves client and peer-worker requests.
_Avoid_: server, node, cache server (reserve _node_ for the abstract membership entity the master tracks).

**Master**:
The metadata and coordination authority that tracks worker membership, owns object/stream metadata routing, and drives
hash-ring changes and recovery.
_Avoid_: metadata server, controller, coordinator (the _Coordinator_ is a separate component).

**Embedded worker**:
A worker run in the same process as the client (via `InitEmbedded`), as opposed to a standalone worker reached over a
connection.
_Avoid_: in-process server, local server.

**Node**:
The abstract cluster member the master records via a `NodeDescriptor` (type, address, state, last-heartbeat); a worker is
the concrete process occupying a node.
_Avoid_: host, machine, instance.

**Tenant**:
The isolation boundary (`tenantId`) that scopes object ownership and authorization for a set of clients.
_Avoid_: namespace, account, project.

## Client surface

**DsClient**:
The aggregate client facade that bundles `KVClient`, `ObjectClient`, and `HeteroClient` so one connection can use all
three cache capabilities.
_Avoid_: DataSystemClient, unified client.

**Object cache**:
The object-style data API family (`ObjectClient`) and its worker-side services, centered on create/put/get of keyed
objects with global reference counting.
_Avoid_: blob store, object store.

**KV cache**:
The key-value API family (`KVClient`) for set/get/delete of keyed values; shares the object-cache client backend
(`ObjectClientImpl`) but presents value-oriented semantics.
_Avoid_: state cache, kvstore (reserve _kvstore_ for the internal metadata store backend).

**Stream cache**:
The producer/consumer streaming API family (`StreamClient`, `Producer`, `Consumer`) with its own client and worker
implementation paths, separate from object/KV.
_Avoid_: message queue, pubsub.

**Hetero cache**:
The heterogeneous-device data path (`HeteroClient`) that moves data between host memory and accelerator (device) memory.
_Avoid_: GPU cache, device cache, tensor cache.

## Object and KV data model

**Object**:
A keyed unit of cached data in the object/KV caches, described by metadata (size, version, life state, location, write
mode, consistency type) and addressed by an object key.
_Avoid_: item, entry, record, value (use _value_ only for the raw bytes a KV `Set` stores).

**Object key**:
The client-supplied or generated identifier for an object; constrained to `a-zA-Z0-9~!@#$%^&*.-_`, under 256 chars.
_Avoid_: object id, oid, name (the code uses both `objectKey` and `objectId`; prefer **object key**).

**Buffer**:
A handle to an object's shared-memory (or fallback heap) region that the client fills and then publishes; supports
mutable access plus read/write latches.
_Avoid_: blob, page, memory block.

**Publish / Seal**:
The two ways a filled buffer is committed to the worker — **Publish** commits mutable data; **Seal** commits immutable
data. Both may declare nested keys.
_Avoid_: commit, flush, write (for the commit step specifically).

**Nested object**:
An object that another object declares a dependency on at publish/seal time, so its lifetime is tracked together with the
parent.
_Avoid_: child object, sub-object, linked object.

**Global reference (GRef)**:
A cluster-wide reference count, held at the master, that gates safe deletion of an object; adjusted via `GIncreaseRef` /
`GDecreaseRef` and queryable as a global ref num.
_Avoid_: refcount, pin count, global lease.

**Write mode**:
The L2-cache persistence policy chosen per write: none, write-through, write-back, and their evictable variants.
_Avoid_: persistence mode, cache mode, durability level.

**Consistency type**:
The memory-consistency model declared for an object — `PRAM` or `CAUSAL`.
_Avoid_: consistency level, ordering mode.

**L2 cache**:
The secondary, durable persistence tier (backed by an external/cloud object store) behind the in-memory cache; objects
reach it via write-through or write-back per their write mode.
_Avoid_: disk tier, cold storage, backing store.

**Spill**:
Moving an object's data from in-memory cache to local disk under memory pressure, distinct from pushing it to the L2
cache.
_Avoid_: page-out, swap, offload.

**Eviction**:
Removing objects from the in-memory cache when capacity is exceeded, governed by an eviction policy and applied before or
alongside spill.
_Avoid_: purge, reclaim, expiry (reserve _expiry_ for TTL-driven removal).

## Stream cache model

**Stream**:
An ordered, append-only sequence of elements that producers write and consumers read, configured with page size, max
size, retention, and a stream mode (`MPMC`/`MPSC`/`SPSC`).
_Avoid_: topic, channel, log, queue.

**Producer**:
A client-side writer that appends elements to a stream, buffering locally and auto-flushing on a delay.
_Avoid_: publisher, writer, sender.

**Consumer**:
A client-side reader bound to a subscription that receives, acknowledges, and tracks elements from a stream.
_Avoid_: subscriber, reader, receiver.

**Subscription**:
A named read position and delivery policy on a stream, typed as `STREAM`, `ROUND_ROBIN`, or `KEY_PARTITIONS`, with its
own cache capacity and prefetch setting.
_Avoid_: consumer group, sub, channel binding.

**Element**:
The unit of data carried through a stream, identified by an element id used for acknowledgement.
_Avoid_: message, event, record, item.

**Cursor**:
A consumer's position in a stream, advanced as elements are received and acknowledged (e.g. last-ack cursor).
_Avoid_: offset, pointer, marker.

**Page (stream)**:
A fixed-size allocation unit of stream storage; a stream's buffer is a sequence of pages.
_Avoid_: chunk, segment, block.

## Heterogeneous device path

**Blob / DeviceBlobList**:
A `Blob` is a (pointer, size) span of device memory; a `DeviceBlobList` groups blobs that live on the same device card,
with a device index and source offset.
_Avoid_: buffer (reserve _Buffer_ for the object-cache shared-memory handle), tensor segment.

**H2D / D2H / D2D**:
The three device transfer directions — host-to-device, device-to-host, device-to-device — naming the hetero operations
(`MGetH2D`/`MSetD2H`, and the D2D `Dev*` calls).
_Avoid_: upload/download, copy-in/copy-out.

**DevPublish / DevSubscribe**:
The one-shot device object exchange pair: publish exposes device memory as a heterogeneous object, subscribe consumes it
over the D2D channel and then auto-deletes the object.
_Avoid_: device put/get (reserve those for the persistent `DevMSet`/`DevMGet` pair, which does not auto-delete).

## Cluster distribution, coordination, and recovery

**Hash ring**:
The consistent-hashing structure mapping object keys to owning workers; the master maintains it (`HashRingPb`) and
publishes membership and range changes to drive routing.
_Avoid_: consistent hash, ring topology, DHT.

**Hash token**:
A worker's position(s) on the hash ring (`hash_tokens`, in `[0, UINT32_MAX]`) that determine which key ranges it owns.
_Avoid_: vnode, ring position, virtual node.

**Slot**:
A unit of key-space ownership on the hash ring used as the granularity of recovery and migration; a worker owns a set of
slots. (Note: `l2cache` reuses _slot_ for an unrelated unit of persisted-data organization with its own manifest —
disambiguate as **L2 slot** when needed.)
_Avoid_: partition, shard, bucket.

**Worker UUID**:
The stable identity of a worker (`worker_uuid`) used to distinguish process incarnations across restarts, separate from
its current address.
_Avoid_: worker id, node id (the code mixes `workerId` and `worker_uuid`; prefer **worker UUID** for identity).

**Metastore**:
The built-in metadata/coordination store (`common/kvstore/metastore`) intended to replace an external ETCD deployment in
some cluster modes.
_Avoid_: built-in etcd, internal kvstore.

**ETCD**:
The external metadata and coordination backend used for cluster state in deployments that do not use the Metastore.
_Avoid_: external store, registry.

**Coordinator**:
The lightweight coordination component (`coordinator.proto`: Put/Range/Watch/KeepAlive) providing etcd-like primitives
without depending on the full master.
_Avoid_: master (distinct component), etcd (a backend, not this service).

**Scale down / Scale up**:
Cluster membership changes — **scale down** is a worker leaving (a `need_scale_down` _voluntary scale down_ migrates data
before exit); **scale up** adds workers and rebalances the ring.
_Avoid_: drain/join, decommission/provision.

**Slot recovery**:
The master-coordinated process that reassigns and rebuilds the slots a failed worker owned, tracked as recovery tasks
(`RecoveryTaskPb`: failed/owner/source worker, slot set, status).
_Avoid_: failover, rebalance, repair.

**Reconciliation**:
The periodic master↔worker synchronization that detects and repairs diverged object metadata state.
_Avoid_: sync, healing, gossip.

**Arena**:
A shared-memory region managed for allocation and isolated by service (object vs stream) and tenant; the backing store
for buffers and stream pages.
_Avoid_: pool, heap, segment, slab.

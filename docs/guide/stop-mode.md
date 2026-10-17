---
title: The socket proxy
order: 47
summary: An optional helper container so BackupHaven can see, stop and start containers.
---

# The socket proxy

BackupHaven never gets control of Docker. Instead, an optional small helper
container, **socket-proxy** from linuxserver.io, passes on just three kinds of
request: *list the containers*, *stop one*, *start one*. Everything else is
refused. Even if BackupHaven were misused, the most it could do through the
proxy is stop or start a container. It never stops itself or the proxy.

With the proxy, BackupHaven:

- shows on the **Apps** page whether each container is **Running** or
  **Stopped**;
- knows exactly which apps are **Not installed** (there's no container with
  that name), instead of guessing from missing folders.

Without the proxy, everything else works as before.

## Setting it up

1. **Make a private network** for the two containers. Open Unraid's
   **Terminal** (the `>_` icon) and run:

   ```
   docker network create backuphaven
   ```

2. **Install the proxy:** **Apps** → search **socket-proxy** → choose the one
   by **linuxserver** → **Install**. Before pressing **Apply**:
   - **Network Type**: `Custom: backuphaven`
   - **CONTAINERS**: `1`
   - **ALLOW_START**: `1`
   - **ALLOW_STOP**: `1`
   - **POST**: `0`. Leave every other permission at `0`.
   - No port mapping is needed: only BackupHaven talks to it, over the private
     network.

3. **Point BackupHaven at it:** **Docker** → BackupHaven → **Edit**:
   - **Network Type**: `Custom: backuphaven`. Your web UI port (7272) stays
     the same.
   - **Show more settings** → **Docker socket proxy**:
     `tcp://socket-proxy:2375` (if you named the proxy container differently,
     use that name).
   - **Apply**.

4. **Check it:** **Settings → Containers (socket proxy)** should say
   **Connected** with the number of containers.

If it says **Not answering**, check both containers are on the `backuphaven`
network and the proxy's name matches the address. If it says the proxy
**refused**, check `CONTAINERS`, `ALLOW_START` and `ALLOW_STOP` are `1`.

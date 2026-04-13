# Distributed Inference Overview

## P2P Connection and cluster initialization

After each node starts, it will try to connect to the signal server specified by the `PARAMIND_SIGNAL_SERVER` environment variable. On successful connection, the node will send a register message to the signal server, together with its unique identifier (UUID), group id that it wants to join and the list of available resources (e.g., GPU, CPU, memory). The signal server will maintain a registry of all connected nodes and their resources. After registration, the signal server will send back a list of all other nodes that are currently registered in the same group, along with their resource information. This allows the newly joined node to start establishing direct P2P connections with other nodes.

For other nodes that are already registered in the same group on the signal server, the signal server will send a notification to them about the new node joining. Upon receiving this notification, these nodes will establish a direct peer-to-peer (P2P) connection with the new node by sending hole punching messages directly to the new node. After both sides receive the hole punching messages, they will establish a direct P2P connection and send a punchSuccess.

## Coordinator Initialization

Each group will have a coordinator node that is responsible for managing the cluster and orchestrating the distributed inference tasks. Initially, the coordinator will be the first node that joins the group. If the coordinator node leaves the group, a new coordinator will be elected from the remaining nodes based on their joining time.

Upon coordinator initialization, the coordinator will gather resource information from all connected nodes in the group and start a shard planning process. After the shard planning is completed, the coordinator will broadcast the shard plan to all nodes in the group. Each node will then load its local shard based on the received shard plan and start listening for inference requests.

## Shard Planning Creiteria and Algorithm

TODO: Add shard planning criteria and algorithm details here.

## Peer Joining, leaving and replanning

When a new peer joins or leaves the group, the coordinator will be notified by the signal server. Upon receiving the notification, the coordinator will trigger a new shard planning process to redistribute the model shards among the remaining nodes in the group. The coordinator will then broadcast the updated shard plan to all nodes.

An exception is when the coordinator node itself leaves the group. In this case every remaining node will check the node list and agree on the new coordinator based on their joining time. The node who finds itself as the new coordinator will trigger a new shard planning process and broadcast the updated shard plan to all nodes.

## Inference Path

When an inference is requested, the requested node will find a inference path that starts from the node holding the first shard of the model and then goes through the nodes holding the subsequent shards until it reaches the node holding the last shard. The input and hidden states will be transmitted along the inference path, and the final output will be sent back to the requested node to form a complete inference response.

To correctly maintain relative information about each inference, a unique inference id will be generated for each inference request. Each node will maintain a kv_cache and a current result (generated tokens) for each inference id. 

During input and hidden state transmission, the inference id and the inference path will be included in the message header. Each node will use the inference id to look up the corresponding kv_cache, and use the inference path to determine where to send the intermediate results after processing its local shard.

In special cases, there could be a change of inference path during the inference process, for example when a node in the inference path leaves the group and a replan is completed by the coordinator. In this case, each node will compare their current local plan with the inference path included in the message header. If an inconsistency is detected, the node will abort the current inference and send an error message back to the requested node.

## Tensor Transmission

The inputs, hidden states and the final output will be transmitted in a tensor format. Tensor packets are large and can frequently exceeds the maximum size a UDP packet could carry, which can lead to UDP packet loss. To solve this problem, tensor packets will be split into multiple chunks and attached with a stream_id and a chunk_id. The receiving node will reassemble the chunks based on the stream_id and chunk_id to reconstruct the original tensor packet.

To further improve the reliability of tensor transmission, a simple retransmission mechanism is implemented. After sending out all chunks of a tensor packet, the sender will start a timer and wait for any NACK messages from the receiver. If a NACK message is received, the sender will retransmit the requested chunks. If the timer expires without receiving any NACK messages, the sender will consider the tensor packet successfully transmitted.

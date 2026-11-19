# FlowTasks 2.0 – DynamoDB Data Model

**Environment:** Development  
**AWS Region:** ap-south-1 (Mumbai)  
**Capacity mode:** PAY_PER_REQUEST  
**Deletion strategy:** Soft delete

## 1. Design objectives

- Avoid full-table scans for normal application queries.
- Use Cognito `sub` as the stable user identifier.
- Support efficient task listing for users and administrators.
- Preserve audit history and deleted task records.
- Maintain consistency across related DynamoDB writes.
- Support pagination and future growth.

## 2. Tables

| Table | Partition key | Sort key | Purpose |
|---|---|---|---|
| FlowTasks-Dev-Tasks | pk | sk | Canonical task records |
| FlowTasks-Dev-TaskMembership | pk | sk | User-task relationships and admin listing |
| FlowTasks-Dev-Comments | pk | sk | Task comments |
| FlowTasks-Dev-Mentions | pk | sk | User notifications |
| FlowTasks-Dev-Audit | pk | sk | Audit history |

All tables use on-demand capacity and point-in-time recovery.

## 3. Tasks table

Primary key:
- `pk = TASK#<task_id>`
- `sk = META`

Important attributes:
- taskId
- title
- description
- category
- status
- priority
- ownerId
- ownerName
- participantIds
- participants
- createdBy
- createdAt
- updatedBy
- updatedAt
- isDeleted
- deletedAt (when deleted)
- deletedBy (when deleted)
- commentCount

This is the canonical source for task details.

## 4. TaskMembership table

Primary key:
- `pk = TASK#<task_id>`
- `sk = USER#<cognito_sub>` for user membership
- `sk = ADMIN` for the admin listing record

### GSI1 – User task listing

- Partition key: `gsi1pk`
- Sort key: `gsi1sk`

User membership attributes:
- `gsi1pk = USER#<cognito_sub>`
- `gsi1sk = UPDATED#<timestamp>#TASK#<task_id>`
- taskId
- userId
- role (OWNER or PARTICIPANT)
- taskTitle
- status
- priority
- updatedAt

### GSI2 – Admin task listing

- Partition key: `gsi2pk`
- Sort key: `gsi2sk`

Admin index attributes:
- `gsi2pk = ADMIN#<shard>`
- `gsi2sk = UPDATED#<timestamp>#TASK#<task_id>`
- taskId
- updatedAt

Use four deterministic shards: ADMIN#0, ADMIN#1, ADMIN#2 and ADMIN#3. Query all four shards for admin listing and merge results by timestamp.

## 5. Comments table

Primary key:
- `pk = TASK#<task_id>`
- `sk = COMMENT#<timestamp>#<comment_id>`

Attributes:
- taskId
- commentId
- comment
- userId
- userName
- createdAt
- updatedAt

## 6. Mentions table

Primary key:
- `pk = USER#<cognito_sub>`
- `sk = MENTION#<timestamp>#<mention_id>`

Attributes:
- taskId
- taskTitle
- commentId (when applicable)
- comment
- mentionedBy
- status (UNREAD or READ)
- createdAt
- readAt

GSI1 for unread notifications:
- Partition key: `gsi1pk = USER#<cognito_sub>#UNREAD`
- Sort key: `gsi1sk = <timestamp>#<mention_id>`

Remove the GSI1 attributes when a mention is marked READ.

## 7. Audit table

Primary key:
- `pk = AUDIT#YYYY-MM`
- `sk = <timestamp>#<event_id>`

Attributes:
- action
- taskId
- taskTitle
- actorId
- actorName
- oldValue (when applicable)
- newValue (when applicable)
- createdAt

Audit queries will retrieve the relevant month partitions and merge results in descending timestamp order.

## 8. Consistency and business rules

1. Task creation writes the task, membership records, admin listing record and audit record transactionally.
2. Task updates maintain the relevant membership records and admin listing record.
3. Comment creation and comment count updates should be transactional.
4. Mention identities use Cognito `sub`, not mutable usernames.
5. Soft deletion sets `isDeleted = true` and removes active listing records. Original task data, comments and audit history remain preserved.
6. Only authorized users can read or modify tasks and comments.
7. List endpoints support pagination.
8. Enforce a participant/mention limit that keeps transactional operations within DynamoDB limits.

## 9. Operational configuration

- Billing mode: PAY_PER_REQUEST
- Point-in-time recovery: Enabled
- Encryption at rest: AWS-owned DynamoDB encryption keys initially
- TTL: Not enabled initially; retention policy will be designed separately
- Region: ap-south-1
- Environment prefix: FlowTasks-Dev

## 10. Implementation order

1. Implement the data access patterns.
2. Update task creation and task listing.
3. Update task modification and soft deletion.
4. Update comments and comment counts.
5. Update mentions and read status.
6. Update audit recording and retrieval.
7. Manually verify application workflows.
8. Add automated tests before the CI/CD production workflow.

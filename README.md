CRUD App – Serverless Task Management System (AWS)

** Live Application:**
(https://dg8837j63ledy.cloudfront.net/login.html)

** Overview**

This project is a fully serverless, multi-user Task Management CRUD application built using AWS services.
It supports task creation, updates, comments, mentions, auditing, and role-based access, all without managing any servers.

The system is designed with real-world architecture principles:
	•	Event-driven backend
	•	Secure authentication
	•	Scalable data storage
	•	Clean frontend–backend separation

** Key Features**

 Task Management
	•	Create tasks with:
	•	Title
	•	Description
	•	Category
	•	Status (todo / in-progress / done)
	•	Priority (low / medium / high)
	•	Update task status & priority inline
	•	Delete tasks (admin or owner only)

 **Comments & Mentions**
	•	Add comments to tasks
	•	Edit & delete own comments
	•	Mention single or multiple users using @username
	•	Mentions trigger notifications and task visibility

 **Multi-User Collaboration**
	•	Tasks are automatically shared with mentioned users
	•	Mentioned users:
	•	See the task in their task list
	•	Receive mention notifications
	•	Supports multiple mentions per task or comment

** Mentions & Notifications**
	•	Dedicated Mentions View
	•	Unread / read status
	•	Clicking a mention opens the related task

 **Role-Based Access Control**
	•	Admin
	•	View all tasks
	•	Access audit logs
	•	User
	•	View only tasks they participate in
	•	Modify only their own tasks/comments

** Audit Logging**
	•	Tracks:
	•	Task creation
	•	Updates
	•	Deletions
	•	Comments
	•	Admin-only audit view for accountability

** Architecture**

1.Frontend
	•	HTML + CSS + Vanilla JavaScript
	•	Hosted as static files
	•	Communicates with backend via REST APIs

2.Backend (Serverless)
	•	AWS Lambda – Business logic
	•	Amazon API Gateway – REST endpoints
	•	Amazon DynamoDB – Data storage
	•	Amazon Cognito – Authentication & authorization
	•	IAM Roles & Policies – Fine-grained security

3.Data Model (DynamoDB)
	•	Single-table design using:
	•	pk (partition key)
	•	sk (sort key)
	•	Supports:
	•	Tasks
	•	Comments
	•	Mentions
	•	Audit logs
	•	Optimized for scalability and cost

  Security Highlights
	•	JWT-based authentication using Cognito
	•	API Gateway authorizers
	•	Least-privilege IAM roles
	•	No credentials stored in frontend
	•	Role-based access enforcement in Lambdas


** Why This Project Matters**

This is not a tutorial project.

It demonstrates:
	•	Real AWS serverless architecture
	•	Clean separation of concerns
	•	Production-style access control
	•	Multi-user collaboration patterns
	•	Auditability & traceability

**  Final Notes

This application was built with free-tier friendly AWS services, focusing on architecture clarity, correctness, and scalability.**

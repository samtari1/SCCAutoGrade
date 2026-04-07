-- Part 1: Simple View
CREATE VIEW simple_view AS
	SELECT student_id, first_name FROM students;
    
-- Part 2: View with JOIN
CREATE VIEW join_view AS
	SELECT CONCAT(first_name, ' ', last_name) AS student_name, c.course_name, e.grade
    FROM students s
		JOIN enrollments e ON s.student_id = e.enrollment_id
        JOIN courses c ON s.student_id = c.course_id;
        
-- Query to see all records
SELECT * FROM university.join_view;

-- Part 3: Aggregate View
CREATE VIEW aggregate_view AS
SELECT AVG(grade) AS grade_avg, CONCAT(first_name, ' ', last_name) AS student_name
    FROM students s
	JOIN enrollments e ON s.student_id = e.enrollment_id
	GROUP BY student_name;
    
-- Query to view the averages, shows the grades in descending order
SELECT * FROM university.aggregate_view
ORDER BY grade_avg DESC;

-- Part 4: Security with Views
CREATE VIEW student_public_info_view AS
	SELECT CONCAT(first_name, ' ', last_name) AS student_name, major
    FROM students;

'report_user' can only see the name and major of a student, not grades or any other columns
GRANT SELECT ON student_public_info_view TO 'report_user';

-- Part 5: Advanced View
CREATE VIEW advanced_view AS
	SELECT CONCAT(first_name, ' ', last_name) AS student_name, major, AVG(e.grade) AS avg_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.enrollment_id
    WHERE major = 'Computer Science'
    GROUP BY student_name, major
    ORDER BY avg_grade
	LIMIT 5;
   
Select query to show the results of the view
SELECT * FROM university.advanced_view;

-- Part 6: Combined View Usage
CREATE VIEW combined_view AS
	SELECT AVG(grade) AS grade_avg, last_name, gpa
    FROM students s
	JOIN enrollments e ON s.student_id = e.enrollment_id
    WHERE gpa > 3.5
	GROUP BY last_name, gpa
    ORDER BY last_name;
   
-- Query to show results of the combined view   
SELECT * FROM university.combined_view;

-- Part 7: Short Answer Questions

-- 1. What are the main benefits of using views in MySQL?
-- 	  The benefits of using views are that they can simpliy complex queries, improve security by providing access to specific columns/rows to only certain roles,
--    they enhance data abstraction to hide table structure changes, and improve readability and reuse.

-- 2. How do views improve database security?
--    Views can improve database security by granting access only to the selected view and not the base table itself so the underlying data remains protected.

-- 3. How does a view help hide table structure changes from users?
--    A view can help hide table structure changes from users by abstracting the table from the user.

-- 4. Why might you use an aggregate function in a view?
--    You would use an aggregate function in a view to simplify complex calculations and summarize information.

-- 5. Explain the difference between querying a table directly and querying through a view.
--    The difference between querying a table directly and querying through a view is that tables can store data, barely restrict access, and can't simplify queries.
--    With views it doesn't store data, only displays it, can restrict access to other users, and can simplify queries.    
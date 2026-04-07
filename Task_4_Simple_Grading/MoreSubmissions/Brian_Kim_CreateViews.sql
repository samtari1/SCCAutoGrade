-- Part 1: Simple View
CREATE VIEW simple_view_brian AS
    SELECT student_id, first_name, last_name
    FROM students;

SELECT * FROM simple_view_brian;

-- Part 2: View with JOIN
CREATE VIEW join_view_brian AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           e.course_id AS course_name,
           e.grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id;

SELECT * FROM join_view_brian;

-- Part 3: Aggregate View
CREATE VIEW aggregate_view_brian AS
    SELECT CONCAT(first_name, ' ', last_name) AS student_name,
           AVG(gpa) AS average_grade
    FROM students
    GROUP BY student_id;

SELECT * FROM aggregate_view_brian
ORDER BY average_grade DESC;

-- Part 4: Security with Views
CREATE VIEW student_public_info_brian AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           s.major,
           e.grade
    FROM students s
    LEFT JOIN enrollments e ON s.student_id = e.student_id;

GRANT SELECT ON student_public_info_brian TO 'report_user';

-- Part 5: Advanced View
CREATE VIEW top_cs_students_brian AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           s.major,
           AVG(e.grade) AS avg_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    WHERE s.major = 'CS'
    GROUP BY s.student_id, s.first_name, s.last_name, s.major
    ORDER BY avg_grade DESC
    LIMIT 5;

SELECT * FROM top_cs_students_brian;

-- Part 6: Combined View Usage
CREATE VIEW combined_view_brian AS
    SELECT last_name, gpa
    FROM students;

SELECT * FROM combined_view_brian
ORDER BY last_name;

-- Part 7: Short Answer Questions
-- 1) Views can simplify SQL and improve reuse.
-- 2) Views can hide fields and restrict access.
-- 3) A view can abstract base-table structure.
-- 4) Aggregate functions help summarize results.
-- 5) Direct table queries are raw; views provide abstraction.

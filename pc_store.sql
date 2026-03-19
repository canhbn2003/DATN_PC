DELETE FROM search_history
DELETE FROM user_behavior
DELETE FROM order_items
DELETE FROM orders
DELETE FROM cart_items
DELETE FROM carts
DELETE FROM product_detail
DELETE FROM products
DELETE FROM users
DELETE FROM categories
DBCC CHECKIDENT ('categories', RESEED, 0)
DBCC CHECKIDENT ('products', RESEED, 0)
DBCC CHECKIDENT ('users', RESEED, 0)
DBCC CHECKIDENT ('product_detail', RESEED, 0)
DBCC CHECKIDENT ('carts', RESEED, 0)
DBCC CHECKIDENT ('cart_items', RESEED, 0)
DBCC CHECKIDENT ('orders', RESEED, 0)
DBCC CHECKIDENT ('order_items', RESEED, 0)
DBCC CHECKIDENT ('user_behavior', RESEED, 0)
DBCC CHECKIDENT ('search_history', RESEED, 0)

INSERT INTO categories (name_categories) VALUES
('CPU'),
('GPU'),
('RAM'),
('SSD'),
('Mainboard'),
('PSU'),
('Case'),
('Cooling');
SELECT * FROM categories

INSERT INTO users (name_users,email,password,role) VALUES
('Nguyen Van A','a@gmail.com','123456','user'),
('Tran Van B','b@gmail.com','123456','user'),
('Le Van C','c@gmail.com','123456','user'),
('Pham Van D','d@gmail.com','123456','user'),
('Hoang Van E','e@gmail.com','123456','user'),
('Do Van F','f@gmail.com','123456','user'),
('Le Thi G','g@gmail.com','123456','user'),
('Admin','admin@gmail.com','123456','admin');
SELECT * FROM users 

INSERT INTO products (name_products,brand,price,stock,id_categories)
VALUES
('Intel Core i5 12400F','Intel',4500000,50,1),
('Intel Core i5 13400F','Intel',5200000,40,1),
('Intel Core i7 13700K','Intel',9500000,30,1),
('AMD Ryzen 5 5600X','AMD',4200000,50,1),
('AMD Ryzen 7 7700X','AMD',8500000,25,1),

('RTX 4060 ASUS Dual','ASUS',9000000,20,2),
('RTX 4060 Ti MSI Gaming','MSI',11500000,15,2),
('RTX 4070 Gigabyte Eagle','Gigabyte',15000000,12,2),

('Corsair 16GB DDR4','Corsair',900000,100,3),
('Corsair 16GB DDR5','Corsair',1500000,80,3),

('Samsung 980 Pro 1TB','Samsung',3500000,40,4),
('WD Black SN850X 1TB','WD',3300000,35,4);
SELECT * FROM products
DECLARE @i INT = 1

WHILE @i <= 40
BEGIN

INSERT INTO products (name_products,brand,price,stock,id_categories)
VALUES
(
'PC Component ' + CAST(@i AS NVARCHAR),
CASE WHEN @i % 2 = 0 THEN 'Intel'
     WHEN @i % 3 = 0 THEN 'AMD'
     ELSE 'ASUS'
END,
(ABS(CHECKSUM(NEWID())) % 20000000) + 500000,
(ABS(CHECKSUM(NEWID())) % 100) + 10,
(ABS(CHECKSUM(NEWID())) % 8) + 1
)

SET @i = @i + 1

END


INSERT INTO product_detail (spec_name_product,spec_value_product,id_products)
VALUES
('Socket','LGA1700',1),
('Core','6',1),
('Thread','12',1),
('Base Clock','2.5 GHz',1),

('Memory','8GB',6),
('Memory Type','GDDR6',6),

('Capacity','16GB',9),
('Bus Speed','3200MHz',9),

('Capacity','1TB',11),
('Interface','NVMe PCIe 4.0',11);
select * from product_detail

INSERT INTO carts (id_users)
VALUES
(1),(2),(3),(4),(5),(6),(7);

INSERT INTO cart_items (id_carts,id_products,quantity_cart_items)
VALUES
(1,1,1),
(1,6,1),
(2,3,1),
(3,5,2),
(4,9,1),
(5,7,1);
INSERT INTO orders (total_price_orders,status_orders,id_users)
VALUES
(9000000,'completed',1),
(5200000,'completed',2),
(15000000,'shipping',3),
(1800000,'completed',4);

INSERT INTO order_items (quantity_order_items,price_order_items,id_orders,id_products)
VALUES
(1,9000000,1,6),
(1,5200000,2,2),
(1,15000000,3,8),
(1,1800000,4,10);

DECLARE @i INT = 1

WHILE @i <= 1000
BEGIN

INSERT INTO user_behavior
(action_type_user_behavior,id_users,id_products)

VALUES
(
CASE 
WHEN @i % 5 = 0 THEN 'purchase'
WHEN @i % 3 = 0 THEN 'add_to_cart'
ELSE 'view'
END,

(ABS(CHECKSUM(NEWID())) % 7) + 1,

(ABS(CHECKSUM(NEWID())) % 50) + 1
)

SET @i = @i + 1

END
select * from user_behavior

DECLARE @i INT = 1

WHILE @i <= 200
BEGIN

INSERT INTO search_history (keyword_search_history,id_users)
VALUES
(
CASE 
WHEN @i % 5 = 0 THEN 'RTX'
WHEN @i % 4 = 0 THEN 'Ryzen'
WHEN @i % 3 = 0 THEN 'SSD'
WHEN @i % 2 = 0 THEN 'RAM'
ELSE 'Intel'
END,
(ABS(CHECKSUM(NEWID())) % 7) + 1
)

SET @i = @i + 1

END
SELECT*FROM search_history
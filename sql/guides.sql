-- ============================================================
-- 真人导游对接模块（导游档案 + 预约单）
-- 在 Navicat 中选中 travel 库后整段执行；或云端：
--   docker exec -i travel-mysql sh -c "mysql -uroot -p$MYSQL_ROOT_PASSWORD travel" < sql/guides.sql
-- 重复执行安全：DROP IF EXISTS + CREATE，种子数据用 INSERT 前先清空。
-- ============================================================

DROP TABLE IF EXISTS guide_bookings;
DROP TABLE IF EXISTS guides;

CREATE TABLE guides (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    guide_no VARCHAR(50) NOT NULL COMMENT '导游工号',
    guide_name VARCHAR(60) NOT NULL COMMENT '导游姓名',
    area_id BIGINT UNSIGNED NOT NULL COMMENT '服务城市 area_id（areas.id, level=2）',
    city_name VARCHAR(60) NOT NULL COMMENT '服务城市名（冗余，便于展示）',
    level_code VARCHAR(20) NOT NULL DEFAULT 'senior' COMMENT '等级：gold=金牌，senior=高级，standard=标准',
    rating DECIMAL(3,2) NOT NULL DEFAULT 5.00 COMMENT '评分 1.00-5.00',
    review_count INT NOT NULL DEFAULT 0 COMMENT '评价数',
    service_count INT NOT NULL DEFAULT 0 COMMENT '累计带团次数',
    years_experience INT NOT NULL DEFAULT 3 COMMENT '从业年限',
    price_per_day DECIMAL(10,2) NOT NULL COMMENT '每日服务费（元/天）',
    languages VARCHAR(200) NOT NULL DEFAULT '普通话' COMMENT '可服务语言，逗号分隔',
    specialties VARCHAR(500) NOT NULL DEFAULT '' COMMENT '擅长领域，逗号分隔',
    intro VARCHAR(1000) NOT NULL DEFAULT '' COMMENT '自我介绍',
    avatar_url VARCHAR(500) NULL COMMENT '头像地址',
    tags_payload JSON NULL COMMENT '标签数组 JSON',
    status_code VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT '状态：active=可预约，busy=繁忙，inactive=停用',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_guides_no (guide_no),
    KEY idx_guides_area_status (area_id, status_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '真人导游档案表';

CREATE TABLE guide_bookings (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    booking_no VARCHAR(50) NOT NULL COMMENT '预约单号',
    user_id BIGINT UNSIGNED NOT NULL COMMENT '预约用户 users.id',
    guide_id BIGINT UNSIGNED NOT NULL COMMENT '导游 guides.id',
    area_id BIGINT UNSIGNED NOT NULL COMMENT '服务城市 area_id',
    city_name VARCHAR(60) NOT NULL,
    service_date DATE NOT NULL COMMENT '服务日期',
    days INT NOT NULL DEFAULT 1 COMMENT '服务天数',
    people_count INT NOT NULL DEFAULT 1 COMMENT '出行人数',
    total_amount DECIMAL(12,2) NOT NULL COMMENT '预约总金额 = 单价 * 天数',
    status_code VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT '状态：pending=待确认，confirmed=已确认，completed=已完成，cancelled=已取消',
    contact_name VARCHAR(60) NULL COMMENT '联系人',
    contact_phone VARCHAR(30) NULL COMMENT '联系电话',
    remark VARCHAR(1000) NULL COMMENT '备注/游玩需求',
    plan_summary VARCHAR(1000) NULL COMMENT '关联的 AI 行程摘要（联动用）',
    guide_reply VARCHAR(1000) NULL COMMENT '导游确认回复',
    confirmed_at DATETIME NULL,
    cancelled_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_guide_bookings_no (booking_no),
    KEY idx_guide_bookings_user (user_id, id),
    KEY idx_guide_bookings_guide (guide_id),
    CONSTRAINT fk_gb_user FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_gb_guide FOREIGN KEY (guide_id) REFERENCES guides (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '导游预约单表';

-- ==================== 种子数据：6 个主城市，每城 3 名导游 ====================
INSERT INTO guides
(guide_no, guide_name, area_id, city_name, level_code, rating, review_count, service_count,
 years_experience, price_per_day, languages, specialties, intro, avatar_url, tags_payload, status_code) VALUES
-- 三亚 2197
('G1001','陈海风',2197,'三亚','gold',4.98,326,812,9,880.00,'普通话,英语','海岛度假,潜水,亲子','土生土长三亚人，深耕海岛线路9年，熟悉亚龙湾、蜈支洲每一处最佳机位与潜点，擅长亲子与蜜月定制。',NULL,JSON_ARRAY('金牌导游','海岛通','潜水教练'),'active'),
('G1002','林小椰',2197,'三亚','senior',4.92,218,506,6,680.00,'普通话,粤语','美食,旅拍,免税购物','带你吃遍地道海鲜大排档，熟知免税店攻略与旅拍路线，讲解风趣，被游客称为"行走的种草机"。',NULL,JSON_ARRAY('美食达人','旅拍引导','购物顾问'),'active'),
('G1003','王天涯',2197,'三亚','standard',4.85,142,330,4,520.00,'普通话','文化讲解,深度游','专注天涯海角、南山文化旅游区的人文讲解，慢节奏深度游，适合长辈与朋友结伴。',NULL,JSON_ARRAY('文化讲解','耐心细致','长辈友好'),'active'),
-- 成都 2278
('G2001','赵锦城',2278,'成都','gold',4.97,412,960,11,760.00,'普通话,四川话,英语','三国文化,美食,熊猫','成都本地金牌讲解，武侯祠、杜甫草堂如数家珍，带你避开网红店找到最巴适的老字号。',NULL,JSON_ARRAY('金牌导游','三国文化','川菜通'),'active'),
('G2002','孙幺妹',2278,'成都','senior',4.90,289,611,7,580.00,'普通话,四川话','小吃,古镇,休闲','活泼热情的川妹子，宽窄巷子、锦里、都江堰轻车熟路，用摆龙门阵的方式讲成都的烟火气。',NULL,JSON_ARRAY('美食向导','古镇达人','气氛担当'),'active'),
('G2003','周青城',2278,'成都','senior',4.88,176,420,8,620.00,'普通话','大熊猫,自然,摄影','常年带熊猫基地与青城山线路，懂动物习性也懂拍照光线，亲子科普讲解尤其受欢迎。',NULL,JSON_ARRAY('熊猫专家','自然科普','摄影指导'),'active'),
-- 厦门 1154
('G3001','吴海屿',1154,'厦门','gold',4.96,301,733,10,720.00,'普通话,闽南语,英语','鼓浪屿,建筑,海岛','鼓浪屿土著，万国建筑群与音乐文化信手拈来，带你走游客罕至的小众老街与最美日落点。',NULL,JSON_ARRAY('金牌导游','鼓浪屿通','建筑人文'),'active'),
('G3002','郑阿莎',1154,'厦门','senior',4.91,244,528,6,560.00,'普通话,闽南语','美食,环岛路,拍照','熟悉沙坡尾、曾厝垵的文艺小店与地道小吃，擅长帮游客拍出海岛度假大片。',NULL,JSON_ARRAY('文艺线路','美食达人','旅拍'),'active'),
('G3003','何鹭江',1154,'厦门','standard',4.83,138,297,5,480.00,'普通话','南普陀,人文,茶道','慢节奏人文线路，南普陀寺与茶道体验讲解细致，适合想要静心放松的游客。',NULL,JSON_ARRAY('人文讲解','茶道体验','舒缓节奏'),'active'),
-- 杭州 915
('G4001','沈西湖',915,'杭州','gold',4.98,388,890,12,780.00,'普通话,英语','西湖,园林,宋文化','西湖边长大的金牌讲解，苏堤白堤的四季、灵隐飞来石造像的故事讲得淋漓尽致，宋韵文化功底深厚。',NULL,JSON_ARRAY('金牌导游','西湖通','宋文化'),'active'),
('G4002','陆龙井',915,'杭州','senior',4.89,205,478,7,600.00,'普通话','茶道,古镇,徒步','龙井茶文化专家，梅家坞采茶、九溪徒步、西塘乌镇古镇线路都有独到玩法。',NULL,JSON_ARRAY('茶文化','徒步线路','古镇'),'active'),
('G4003','苏堤春',915,'杭州','standard',4.84,156,352,5,500.00,'普通话','亲子,摄影,夜游','擅长亲子与夜游线路，西湖音乐喷泉、京杭大运河夜景的绝佳观赏点烂熟于心。',NULL,JSON_ARRAY('亲子友好','夜游专家','摄影'),'active'),
-- 张家界 1857
('G5001','田峰林',1857,'张家界','gold',4.97,355,806,10,860.00,'普通话,英语','峰林地貌,徒步,摄影','土家族金牌导游，张家界国家森林公园、袁家界、天子山 every 观景台的最佳时间与角度都了如指掌。',NULL,JSON_ARRAY('金牌导游','地貌专家','徒步达人'),'active'),
('G5002','向天门',1857,'张家界','senior',4.90,198,455,6,640.00,'普通话','天门山,玻璃栈道,刺激项目','专带天门山与玻璃栈道线路，熟悉索道与快线索排班，帮你避开人流高峰。',NULL,JSON_ARRAY('天门山通','避峰专家','刺激线路'),'active'),
('G5003','覃溪谷',1857,'张家界','standard',4.86,121,268,4,520.00,'普通话','金鞭溪,自然,慢游','主打金鞭溪与溪谷慢游，讲解细致、节奏舒缓，适合体力一般的游客与摄影爱好者。',NULL,JSON_ARRAY('慢游线路','自然讲解','耐心细致'),'active'),
-- 云南 2574
('G6001','和玉龙',2574,'云南','gold',4.98,402,918,11,820.00,'普通话,英语,纳西语','雪山,古城,深度游','纳西族金牌导游，丽江古城、玉龙雪山、束河古镇如数家珍，擅长多日深度定制与文化解读。',NULL,JSON_ARRAY('金牌导游','雪山线路','民族文化'),'active'),
('G6002','杨洱海',2574,'云南','senior',4.92,267,590,7,640.00,'普通话','洱海,环湖,旅拍','大理洱海环湖线路专家，喜洲、双廊、海舌生态公园的最佳机位与日出日落时间门儿清。',NULL,JSON_ARRAY('洱海通','旅拍专家','环湖线路'),'active'),
('G6003','段石林',2574,'云南','standard',4.85,149,341,5,520.00,'普通话','石林,喀斯特,科普','昆明石林与喀斯特地貌讲解生动，适合亲子科普与初次来云南的游客。',NULL,JSON_ARRAY('地貌科普','亲子友好','昆明周边'),'active');

SELECT CONCAT('导游种子数据已写入：', COUNT(*), ' 条') AS result FROM guides;

# 病床事件定义

id为事件号、patient为患者号、bed为床号，全部保留字符串及前导零。ward支持W1/W2或中文病区名；床号001–004属于一病区，101–104属于二病区。
time需带偏移，统一UTC。type支持admit、transfer、discharge。同一患者按事件时间排序，同刻先出院、再转床、再入院。
scope为可选编号域，取值ER（急诊，兼容中文“急诊”）或IP（住院，兼容“住院”），缺省视为IP；旧文件无此列时全部按IP处理。急诊与住院各发一套患者号，同一人转住院后编号改变。
占床段[start,end)，终点不重复计数。缺出院延长至observation_end并标记open。转床关闭前床并打开后床。没有入院的出院进入隔离。
同事件最高version采信；同版相互矛盾则全部隔离。完全重复和旧版单列。双占通过区间交集计算分钟，不直接删除来源事件。
当地日历固定+08:00，午夜切分，分钟为Decimal。分段合计应等于日历合计，双占另外列明。迟到基于文件原始行序比较既有最大事件时间，晚到仍完整回放。
复核版本递增，旧版更新409。输入文件与参数纳入运行ID，内容不变重跑不增行。空值不可作为零时长。

## 编号对应关系（identity_links）

对应关系独立于运行批次持久保存于SQLite，只增不删：字段link_id（L0001起，序号不复用）、er_patient、ip_patient、status（active/corrected）、replaces（被更正的旧link_id）、created、source_run、note。
部分唯一索引保证任一时刻一个急诊号、一个住院号各只有一条active对应。POST /api/identity/links提交：同一对编号重试返回既有行且created=false，不新增；某编号改指他人时旧行置corrected并插入新行，新行replaces指向旧行；两个编号各自存在不同生效对应时返回LINK_CONFLICT且不留半条。
POST /api/identity/links/<link_id>/unlink撤销错误对应，同样仅置corrected。编号可凭source_run在批次事件中校验存在性与编号域，不符返回LINK_UNKNOWN。

## 身份接回（M8）

M8不改动M1–M7，而是从M2全量采信事件（m2_records，含M3按ORPHAN隔离前的断链出院）独立按“住院人”重建。canonical person取ER:急诊号；未关联的IP编号自成IP:住院号。
跨编号域入院（ER链上的IP admit）按接续处理：ER链仍开放则在入院时刻关闭ER最后占床段；ER链已关闭且同床则补回两段之间的空档（占床不再断成两截、床位空档不再误判可收治），异床则各自闭区间、人链连续。同刻事件ER先于IP。
每个接回占床段带link_id与basis（“对应关系 Lxxxx”或“无对应（原始编号）”），看板按床显示占用人、原始编号、编号域与依据。人链住院时长los_minutes按人合计；双占、日历、对账在接回结果上重算。
对应关系录错会把两个不同的人并为一人：真实双占因同person过滤而消失、床位占用与时长判错。更正或撤销生效对应后重跑，人链自动拆回原来两条，时长还原；原始事件与历史对应行均不删除。M8守恒：M2采信数=重放采信+M8隔离。


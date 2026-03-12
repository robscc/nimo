1. Skill市场的接入，要支持https://clawhub.ai/ 与https://skills.sh/ 的技能引入，只需要将skill的地址黏贴进对话框安装或者在skill管理页面贴地址安装即可，需要测试通过 https://skills.sh/vercel-labs/skills/find-skills 这个skill 并且通过这个skill安装一个新的skill 方可通过
2. 将服务配置也放到 ~/.nimo/目录下面，用yaml或者json的格式来管理，增强可读性， 完成后也需要测试
3. session上下文管理，在Chat页面每个session上添加元信息展示，包含模型信息、总上下文大小，使用的tools,skill；在session 上配置是否启用skill或者tools，但是范围不能超过整个系统的配置的工具范围，但是如果出现了我系统没配置但是session配置了，那当系统配置了启用skill或者工具的话就自动在session上启用，起到一个全局管理tool或者skills的功能


注意，开发之前都需要提前写好后端的测试用例，测试通过之后并且前后端一起通过playwright 走一遍用例才算完成
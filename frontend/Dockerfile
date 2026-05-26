FROM node:18-alpine AS runner

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

# Run development server to bypass complex Next static builds on local machines
# which optimizes deployment speed and runs perfectly inside Docker/WSL2
EXPOSE 3000

CMD ["npm", "run", "dev"]

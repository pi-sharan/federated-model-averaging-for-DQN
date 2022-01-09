from typing import Dict, List, Tuple
import matplotlib.pyplot as plt

import gym
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim


class ReplayBuffer:
    """A simple numpy replay buffer."""

    def __init__(self, obs_dim: int, size: int, batch_size: int = 32):
        self.obs_buf = np.zeros((size, obs_dim))
        self.acts_buf = np.zeros((size, 1))
        self.rews_buf = np.zeros((size, 1))
        self.next_obs_buf = np.zeros((size, obs_dim))
        self.done_buf = np.zeros((size, 1))
        self.max_size, self.batch_size = size, batch_size
        self.ptr, self.size, = 0, 0

    def store(self,
              obs: np.ndarray,
              act: np.ndarray,
              rew: float,
              next_obs: np.ndarray,
              done: bool,
              ):
        self.obs_buf[self.ptr] = obs
        self.next_obs_buf[self.ptr] = next_obs
        self.acts_buf[self.ptr] = act
        self.rews_buf[self.ptr] = rew
        self.done_buf[self.ptr] = done
        self.ptr = (self.ptr + 1) % self.max_size
        self.size = min(self.size + 1, self.max_size)

    def sample_batch(self) -> Dict[str, np.ndarray]:
        idxs = np.random.choice(self.size, size=self.batch_size, replace=False)
        return (
            torch.FloatTensor(self.obs_buf[idxs]),
            torch.LongTensor(self.acts_buf[idxs]),
            torch.FloatTensor(self.rews_buf[idxs]),
            torch.FloatTensor(self.next_obs_buf[idxs]),
            torch.FloatTensor(self.done_buf[idxs])
        )


class Network(nn.Module):
    def __init__(self, in_dim: int, out_dim: int):
        """Initialization."""
        super(Network, self).__init__()

        self.layers = nn.Sequential(
            nn.Linear(in_dim, 128),
            nn.ReLU(),
            nn.Linear(128, out_dim)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward method implementation."""
        return self.layers(x)


class DQNAgent:
    """DQN Agent interacting with environment."""

    def __init__(
            self,
            env: gym.Env,
            memory_size: int = 10000,
            epsilon_decay_per_step: float = 1 / 2500,
            target_update: int = 100,
            batch_size: int = 32,
            max_epsilon: float = 1.0,
            min_epsilon: float = 0.1,
            gamma: float = 0.99,
            verbose: bool = False,
            network = None):

        obs_dim = env.observation_space.shape[0]
        action_dim = env.action_space.n

        self.env = env
        self.memory = ReplayBuffer(obs_dim, memory_size, batch_size)
        self.batch_size = batch_size
        self.epsilon = max_epsilon
        self.min_epsilon = min_epsilon
        self.epsilon_decay_per_step = epsilon_decay_per_step
        self.target_update = target_update
        self.gamma = gamma
        self.verbose = verbose

        # networks: dqn, dqn_target
        self.dqn = Network(obs_dim, action_dim)
        self.dqn_target = Network(obs_dim, action_dim)
        if(network != None):
            self.dqn.load_state_dict(network.state_dict())
        self.dqn_target.load_state_dict(self.dqn.state_dict())
        self.dqn_target.eval()

        # optimizer
        self.optimizer = optim.Adam(self.dqn.parameters())

        # transition to store in memory
        self.transition = list()

        # mode: train / test
        self.is_test = False

        self.state = self.env.reset()

        self.update_cnt = 0
        self.step_cnt = 1
        self.scores = []
        self.steps_list = []
        self.score = 0
        self.episode = 0

    def select_action(self, state: np.ndarray) -> np.ndarray:
        """Select an action from the input state."""
        # epsilon greedy policy
        if self.epsilon > np.random.random() and not self.is_test:
            selected_action = self.env.action_space.sample()
        else:
            selected_action = self.dqn(
                torch.FloatTensor(state)
            ).argmax()
            selected_action = selected_action.detach().cpu().numpy()

        if not self.is_test:
            self.transition = [state, selected_action]

        return selected_action

    def step(self, using_env, action: np.ndarray) -> Tuple[np.ndarray, np.float64, bool]:
        """Take an action and return the response of the env."""
        next_state, reward, done, _ = using_env.step(action)

        if not self.is_test:
            self.transition += [reward, next_state, done]
            self.memory.store(*self.transition)

        return next_state, reward, done

    def train(self, steps: int):
        """Train the agent."""

        self.is_test = False

        for _ in range(steps):
            self.step_cnt += 1
            action = self.select_action(self.state)
            next_state, reward, done = self.step(self.env, action)

            self.state = next_state
            self.score += reward

            # if episode ends
            if done:
                self.episode += 1
                self.state = self.env.reset()
                self.scores.append(self.score)
                self.steps_list.append(self.step_cnt)
                if(self.verbose == True):
                    print("Step:", self.step_cnt, "\t Episode:", self.episode, "\t reward:",
                        self.score, "\t Avg reward:", sum(self.scores)/len(self.scores))
                if(self.episode % 15 == 0 and self.verbose):
                    self._plot()
                self.score = 0

            # if training is ready
            if self.memory.size >= self.batch_size:
                B_states, B_actions, B_rewards, B_next_states, B_dones = self.memory.sample_batch()

                # G_t   = r + gamma * v(s_{t+1})  if B_states != Terminal
                #       = r                       otherwise
                q_vals = self.dqn(B_states)
                curr_q_value = q_vals.gather(1, B_actions)
                next_q_value = self.dqn_target(
                    B_next_states
                ).max(dim=1, keepdim=True)[0].detach()
                mask = 1 - B_dones
                target = (B_rewards + self.gamma * next_q_value * mask)

                # Method 1: Using the MSE between current and target Qs.
                loss = F.mse_loss(target, curr_q_value).mean()

                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

                self.update_cnt += 1

                # linearly decrease epsilon
                self.epsilon = max(
                    self.min_epsilon, self.epsilon - self.epsilon_decay_per_step)

                # if hard update is needed
                if self.update_cnt % self.target_update == 0:
                    self.dqn_target.load_state_dict(self.dqn.state_dict())
        self.env.close()

    def _plot(self):
        plt.figure(figsize=[12, 9])
        plt.subplot(1, 1, 1)
        plt.title("scores")
        plt.xlabel('Steps:')
        plt.ylabel('Total Reward for episode:')
        plt.plot(self.steps_list, self.scores)
        plt.grid()

        # plt.show()
        plt.savefig('plots/dqn-simple.png')
        plt.close()


if __name__ == "__main__":
    # environment
    env = gym.make("Acrobot-v1")

    agent = DQNAgent(env, verbose=True)

    for i in range(100000):
        agent.train(20)

    ### Testing agent ###
    agent.is_test = True

    state = env.reset()
    done = False
    score = 0

    while not done:
        env.render()
        action = agent.select_action(state)
        next_state, reward, done = agent.step(action)

        state = next_state
        score += reward

    print("score: ", score)
    env.close()
